import re
import io
import zipfile
from collections import defaultdict
import base64
import streamlit.components.v1 as components
import random

import fitz
import streamlit as st
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.pdfbase.pdfmetrics import stringWidth
from streamlit_pdf_viewer import pdf_viewer

st.set_page_config(page_title="Generator Lembar Penilaian Uji Skor", layout="wide")
st.title("Generator Lembar Penilaian Uji Skor Ikan dan Udang")
st.caption("Unggah PDF permohonan PTK. Aplikasi membaca data permohonan dan membuat lembar penilaian PDF baru tanpa membutuhkan file template keluaran.")

# -------------------- EKSTRAKSI PDF SUMBER --------------------

def teks_pdf(data):
    doc = fitz.open(stream=data, filetype="pdf")
    teks = "\n".join(p.get_text("text") for p in doc)
    doc.close()
    return teks

def ambil_nomor(teks):
    m = re.search(r"No\.?\s*:\s*([A-Z0-9][A-Z0-9.\-/]+)", teks, re.I)
    return m.group(1).strip() if m else ""

def buat_kode_aju(nomor, kategori):
    bagian = str(nomor).strip().split("-")

    tahun = bagian[0]
    nomor_urut = bagian[-1]

    if not re.fullmatch(r"20\d{2}", tahun):
        raise ValueError("Tahun pada nomor PTK tidak valid.")

    if not re.fullmatch(r"\d{6}", nomor_urut):
        raise ValueError("Nomor urut PTK tidak valid.")

    akhiran = "1" if kategori == "Ikan" else "2"

    return f"KI/Organo/E.{nomor_urut}-{akhiran}/{tahun}"

def ambil_tanggal(teks):
    m = re.search(r"tanggal\s+(\d{1,2}\s+[A-Za-z]+\s+\d{4})", teks, re.I)
    return m.group(1).strip() if m else ""


# -------------------- EKSTRAKSI MEDIA PEMBAWA --------------------

def norm_word(w):
    return re.sub(r"[^a-z]", "", w[4].lower())


def bersihkan_media(teks):
    teks = re.sub(r"\s+", " ", str(teks)).strip(" \t\r\n-–—")
    teks = re.sub(r"^\d+\.?\s*", "", teks).strip()

    # Hilangkan nama ilmiah dalam tanda kurung.
    teks = re.sub(r"\s*\([^)]*\)", "", teks).strip()

    if not teks:
        return ""

    # Abaikan penanda lampiran.
    if re.fullmatch(r"[-–—\s]*terlampir[-–—\s]*", teks, re.I):
        return ""

    # Abaikan judul kolom.
    if teks.lower() in {
        "media", "pembawa", "media pembawa",
        "nama ilmiah", "kode hs", "bentuk",
        "netto", "jumlah", "satuan", "total"
    }:
        return ""

    return teks


def ekstrak_tabel_media_halaman(page):

    words = page.get_text("words", sort=True)

    if not words:
        return []

    # 1. Cari header Media Pembawa dan Nama Ilmiah.
    kandidat_header = []

    for media in words:

        if norm_word(media) != "media":
            continue

        pembawa = [
            w for w in words
            if norm_word(w) == "pembawa"
            and (
                (
                    abs(w[0] - media[0]) <= 8
                    and 0 <= w[1] - media[1] <= 16
                )
                or
                (
                    0 <= w[0] - media[0] <= 50
                    and abs(w[1] - media[1]) <= 3
                )
            )
        ]

        if not pembawa:
            continue

        pb = min(
            pembawa,
            key=lambda w: abs(w[1] - media[1])
        )

        nama_hdr = [
            w for w in words
            if norm_word(w) == "nama"
            and w[0] > media[0] + 15
            and abs(w[1] - media[1]) <= 4
        ]

        ilmiah_hdr = [
            w for w in words
            if norm_word(w) == "ilmiah"
            and w[0] > media[0] + 15
            and abs(w[1] - media[1]) <= 4
        ]

        pasangan = [
            (n, il)
            for n in nama_hdr
            for il in ilmiah_hdr
            if 0 <= il[0] - n[0] <= 55
            and abs(il[1] - n[1]) <= 3
        ]

        if pasangan:
            n, il = min(
                pasangan,
                key=lambda z: z[0][0]
            )

            kandidat_header.append(
                (media, pb, n, il)
            )

    if not kandidat_header:
        return []

    # Pilih header tabel yang paling atas.
    media, pembawa, nama_hdr, ilmiah_hdr = min(
        kandidat_header,
        key=lambda z: z[0][1]
    )

    y_header_bottom = max(
        media[3],
        pembawa[3],
        nama_hdr[3],
        ilmiah_hdr[3]
    )

    # 2. Cari batas akhir tabel berdasarkan Total.
    total_y = min(
        (
            w[1] for w in words
            if norm_word(w).startswith("total")
            and w[1] > y_header_bottom
        ),
        default=page.rect.height
    )

    # 3. Cari kode HS sebagai penanda setiap baris.
    hs_rows = []

    for w in words:

        angka = re.sub(r"\D", "", w[4])

        if (
            w[1] > y_header_bottom
            and w[1] < total_y
            and w[0] > ilmiah_hdr[0] + 25
            and len(angka) in range(6, 13)
        ):
            hs_rows.append((w[1], w[0]))

    hs_rows.sort()

    # Hilangkan kode HS yang terdeteksi berulang
    # pada posisi baris yang hampir sama.
    hs_unik = []

    for y0, x0 in hs_rows:

        if (
            not hs_unik
            or abs(y0 - hs_unik[-1][0]) > 3
        ):
            hs_unik.append((y0, x0))

    if not hs_unik:
        return []

    # 4. Tentukan batas kolom Media Pembawa.
    # Gunakan jarak antarkata pada baris data pertama
    # untuk membedakan kolom media dan nama ilmiah.

    first_y, hs_x = hs_unik[0]

    first_line = [
        w for w in words
        if media[0] - 45 <= w[0] < hs_x - 35
        and abs(w[1] - first_y) <= 2.5
    ]

    first_line.sort(key=lambda w: w[0])

    gaps = []

    for left, right in zip(
        first_line,
        first_line[1:]
    ):

        gap = right[0] - left[2]

        if gap >= 4:
            gaps.append(
                (gap, (left[2] + right[0]) / 2)
            )

    if not gaps:
        return []

    # Celah terbesar adalah batas kolom Media Pembawa
    # dengan kolom Nama Ilmiah.

    x_max = max(
        gaps,
        key=lambda z: z[0]
    )[1]

    x_min = max(0, media[0] - 45)

    # 5. Ambil setiap baris berdasarkan posisi kode HS.
    hasil = []

    for i, (row_y, _) in enumerate(hs_unik):

        if i + 1 < len(hs_unik):
            next_y = hs_unik[i + 1][0]
        else:
            next_y = total_y

        kandidat = [
            w for w in words
            if x_min <= w[0] < x_max
            and row_y - 2.5 <= w[1] < next_y - 2.5
            and w[1] < total_y
        ]

        kandidat.sort(
            key=lambda w: (round(w[1], 1), w[0])
        )

        # Gabungkan kata berdasarkan baris visual.
        visual_lines = []

        for w in kandidat:

            if (
                not visual_lines
                or abs(
                    w[1] - visual_lines[-1][0][1]
                ) > 2.5
            ):
                visual_lines.append([w])

            else:
                visual_lines[-1].append(w)

        teks_baris = [
            " ".join(
                w[4] for w in sorted(
                    line,
                    key=lambda z: z[0]
                )
            )
            for line in visual_lines
        ]

        nama_media = bersihkan_media(
            " ".join(teks_baris)
        )

        if nama_media and nama_media not in hasil:
            hasil.append(nama_media)

    return hasil


def ambil_media(data):

    doc = fitz.open(
        stream=data,
        filetype="pdf"
    )

    try:

        # PRIORITAS 1:
        # Cari tabel pada lampiran DESCRIPTION OF CONSIGNMENT.

        halaman_lampiran = [
            p for p in doc
            if "DESCRIPTION OF CONSIGNMENT"
            in p.get_text("text").upper()
        ]

        for p in halaman_lampiran:

            hasil = ekstrak_tabel_media_halaman(p)

            if hasil:
                return hasil

        # PRIORITAS 2:
        # Jika tidak ada lampiran, baca tabel B. PERMOHONAN.

        for p in doc:

            hasil = ekstrak_tabel_media_halaman(p)

            if hasil:
                return hasil

        return []

    finally:
        doc.close()


# -------------------- PENENTUAN JENIS --------------------

def jenis_awal(nama):

    n = nama.lower()

    return (
        "Udang"
        if any(
            x in n
            for x in ("udang", "shrimp", "prawn")
        )
        else "Ikan"
    )

# -------------------- PEMBUAT PDF DARI NOL --------------------

def teks_tengah(c, x, y, s, font="Helvetica", size=8):
    c.setFont(font, size)
    c.drawCentredString(x, y, str(s))

def teks_kiri(c, x, y, s, font="Helvetica", size=8):
    c.setFont(font, size)
    c.drawString(x, y, str(s))

def bungkus(teks, font, size, lebar):
    kata = str(teks).split()
    hasil, baris = [], ""
    for k in kata:
        coba = (baris + " " + k).strip()
        if stringWidth(coba, font, size) <= lebar:
            baris = coba
        else:
            if baris:
                hasil.append(baris)
            baris = k
    if baris:
        hasil.append(baris)
    return hasil or [""]

def gambar_header(c, judul, nomor, tanggal, data, kategori):
    W, H = A4
    kiri, kanan = 38, W - 38
    
    # Logo di pojok kiri atas kop
    c.drawImage(
        "logo.png",
        kiri + 5,
        H - 88,
        width=60,
        height=67,
        preserveAspectRatio=True,
        mask="auto",
        anchor="c"
    )
    c.setFont("Helvetica-Bold", 12)
    c.drawCentredString(W/2, H-39, "BADAN KARANTINA INDONESIA")
    c.setFont("Helvetica-Bold", 9)
    c.drawCentredString(W/2, H-53, "BALAI KARANTINA HEWAN, IKAN, DAN TUMBUHAN")
    c.drawCentredString(W/2, H-65, "RIAU")
    c.setFont("Helvetica", 6.5)
    c.drawCentredString(W/2, H-77, "Jln. Pattimura No.10 Pekanbaru, Riau 28131 Telp. (0761) 22172")
    c.drawCentredString(W/2, H-86, "www.karantinaindonesia.go.id")
    c.setLineWidth(1.2)
    c.line(kiri, H-94, kanan, H-94)
    c.line(kiri, H-97, kanan, H-97)
    teks_tengah(c, W/2, H-114, judul, "Helvetica-Bold", 11)

    teks_kiri(c, kiri+25, H-139, ("1. Kode Contoh Uji /No. Aju PTK :" if kategori == "Ikan" else "1. Kode Aju :"), size=8)
    teks_kiri(c, kiri+25, H-152, "2. Tgl. Diterima Contoh Uji :", size=8)
    teks_kiri(c, kiri+175, H-139, nomor, size=7.5)
    teks_kiri(c, kiri+175, H-152, tanggal, size=8)

    # Tabel identitas sampel
    top = H-169
    x = [kiri, kiri+27, kiri+310, kiri+390, kiri+448, kanan]
    headers = ["NO", "JENIS CONTOH UJI", "JUMLAH", "UKURAN", "Ket."]
    for xx in x:
        c.line(xx, top, xx, top-17- (max(1, len(data))*17))
    c.line(kiri, top, kanan, top)
    c.line(kiri, top-17, kanan, top-17)
    c.line(kiri, top-17-(max(1,len(data))*17), kanan, top-17-(max(1,len(data))*17))
    for i, h in enumerate(headers):
        teks_tengah(c, (x[i]+x[i+1])/2, top-12, h, "Helvetica-Bold", 7)
    for i, item in enumerate(data):
        yy = top-29-i*17
        teks_tengah(c, (x[0]+x[1])/2, yy, f"{i+1}.", size=7)
        nama_lines = bungkus(item["nama"], "Helvetica", 7, x[2]-x[1]-6)
        for j, line in enumerate(nama_lines[:2]):
            teks_tengah(c, (x[1]+x[2])/2, yy-j*7, line, size=7)
        teks_tengah(c, (x[2]+x[3])/2, yy, item["jumlah"], size=7)
        teks_tengah(c, (x[3]+x[4])/2, yy, item["ukuran"], size=7)
        teks_tengah(c, (x[4]+x[5])/2, yy, item["ket"], size=7)

    bottom = top-17-(max(1,len(data))*17)
    petunjuk_y = bottom-17
    petunjuk = ("• Berilah tanda √ pada nilai yang dipilih sesuai kode contoh yang diuji." if kategori == "Ikan"
                else "- Berikan tanda √ pada nilai yang dipilih sesuai contoh yang disajikan.")
    teks_kiri(c, kiri+48, petunjuk_y, petunjuk, size=7)

    return petunjuk_y-13

IKAN_ROWS = [
("1. Kenampakan", "", "bold"),
("a. Mata", "", "bold"),
("• Cerah, bola mata menonjol, kornea jernih, kornea dan pupil jernih, mengkilap spesifik jenis ikan", "9", ""),
("• Bola mata rata, kornea agak keruh dan pupil agak keabu-abuan, agak mengkilap spesifik jenis ikan", "7", ""),
("• Bola mata cekung, kornea keruh dan pupil keabu-abuan tidak mengkilap spesifik jenis ikan", "5", ""),
("a. Insang", "", "bold"),
("• Warna insang merah darah atau merah kecoklatan dengan sedikit lendir agak keruh", "9", ""),
("• Warna insang merah tua atau coklat kemerahan, cemerlang dengan sedikit sekali lendir transparan", "7", ""),
("• Warna insang abu-abu atau coklat abu-abu dengan lendir keruh", "5", ""),
("b. Lendir Permukaan Badan", "", "bold"),
("• Lapisan lendir jernih, transparan, mengkilap cerah", "9", ""),
("• Lapisan lendir mulai agak keruh", "7", ""),
("• Lapisan lendir tebal, untuk ikan air laut dan berubah warna", "5", ""),
("2. Daging (warna dan kenampakan)", "", "bold"),
("• Sayatan daging sangat cemerlang spesifik jenis, jaringan daging sangat kuat", "9", ""),
("• Sayatan daging kurang cemerlang, jaringan daging sangat kuat", "7", ""),
("• Sayatan daging mulai pudar, jaringan daging kurang kuat", "5", ""),
("3. Bau", "", "bold"),
("• Bau segar, spesifik jenis kuat", "9", ""),
("• Bau segar, spesifik jenis kurang", "7", ""),
("• Netral, bau asam", "5", ""),
("4. Tekstur", "", "bold"),
("• Padat kompak", "9", ""),
("• Padat, kurang kompak", "7", ""),
("• Kurang padat, tidak kompak", "5", ""),
("TOTAL", "", "bold"),
("RATA-RATA", "", "bold"),
]

UDANG_ROWS = [
("1. Kenampakan", "", "bold"),
("• Utuh, bening bercahaya asli menurut jenis, antar ruas kokoh", "9", ""),
("• Utuh, kurang bening, cahaya mulai pudar, berwarna asli, antar ruas kokoh", "8", ""),
("• Utuh, kebeningan agak hilang, sedikit kusam, antar ruas kurang kokoh", "7", ""),
("• Utuh, kebeningan hilang, kusam, warna agak merah muda, sedikit noda hitam, antar ruas kurang kokoh", "5", ""),
("• Warna merah, noda hitam banyak, kulit mudah lepas dari daging", "3", ""),
("• Warna merah sangat kusam, banyak sekali noda hitam", "1", ""),
("2. Bau", "", "bold"),
("• Bau sangat segar spesifik jenis", "9", ""),
("• Bau segar spesifik jenis", "8", ""),
("• Bau spesifik jenis netral", "7", ""),
("• Mulai timbul bau indol", "6", ""),
("• Bau indol", "5", ""),
("• Bau indol kuat dan bau busuk", "3", ""),
("3. Tekstur", "", "bold"),
("• Sangat elastis, kompak dan padat", "9", ""),
("• Elastis, kompak dan padat", "8", ""),
("• Kurang elastis, kompak dan padat", "7", ""),
("• Tidak elastis, tidak kompak dan tidak padat", "6", ""),
("• Agak lunak", "5", ""),
("• Lunak", "3", ""),
("Total", "", "bold"),
("Rata-rata", "", "bold"),
]

def gambar_tabel_skor(c, y_top, kategori, n_kode):
    W, _ = A4
    kiri, kanan = 38, W-38
    rows = IKAN_ROWS if kategori == "Ikan" else UDANG_ROWS
    # Kolom spesifikasi dan nilai; sisa lebar dibagi sejumlah kode contoh
    spec_w = 310 if kategori == "Ikan" else 245
    nilai_w = 55 if kategori == "Ikan" else 22
    x0, x1, x2 = kiri, kiri+spec_w, kiri+spec_w+nilai_w
    kode_w = (kanan-x2)/n_kode
    tinggi_header = 35 if kategori == "Ikan" else 33
    tinggi_baris = 15 if kategori == "Ikan" else 15
    # Shrink if needed so panelist box remains on page
    max_height = y_top - 105
    total_height = tinggi_header + len(rows)*tinggi_baris
    if total_height > max_height:
        tinggi_baris = max(11, (max_height-tinggi_header)/len(rows))
    y_bottom = y_top - tinggi_header - len(rows)*tinggi_baris

    c.setLineWidth(0.45)
    c.rect(x0, y_bottom, kanan-x0, y_top-y_bottom)
    c.line(x1, y_bottom, x1, y_top)
    c.line(x2, y_bottom, x2, y_top)
    # Garis vertikal kolom kode: tidak membelah header Kode Contoh
    for k in range(n_kode+1):
        xx = x2 + k * kode_w
        c.line(xx, y_bottom, xx, y_top-17)
    # Header
    c.line(x0, y_top-tinggi_header, kanan, y_top-tinggi_header)
    teks_tengah(c, (x0+x1)/2, y_top-20, "Spesifikasi", "Helvetica-Bold", 8)
    teks_tengah(c, (x1+x2)/2, y_top-20, "Nilai", "Helvetica-Bold", 8)
    teks_tengah(c, (x2+kanan)/2, y_top-12, "Kode Contoh", "Helvetica-Bold", 8)
    # Garis horizontal di bawah judul Kode Contoh
    c.line(x2, y_top-17, kanan, y_top-17)
    for k in range(n_kode):
        teks_tengah(c, x2+(k+0.5)*kode_w, y_top-29, str(k+1), "Helvetica-Bold", 7)
    c.line(x2, y_top-tinggi_header, kanan, y_top-tinggi_header)

       # Pilihan nilai yang diperbolehkan
    nilai_diizinkan = {"9", "7"} if kategori == "Ikan" else {"9", "8"}

    # Kelompokkan baris berdasarkan spesifikasi
    kelompok = []
    kelompok_aktif = None

    for i, (label, nilai, style) in enumerate(rows):
        if style == "bold":
            if label.lower().startswith(("total", "rata-rata")):
                kelompok_aktif = None
                continue

            kelompok_aktif = []
            kelompok.append(kelompok_aktif)

        elif nilai in nilai_diizinkan and kelompok_aktif is not None:
            kelompok_aktif.append(i)

    # Pilih satu nilai secara acak untuk setiap spesifikasi
    pilihan_per_kode = []
    total_per_kode = []

    for k in range(n_kode):
        pilihan = {}

        for grup in kelompok:
            if grup:
                indeks = random.choice(grup)
                pilihan[indeks] = True

        pilihan_per_kode.append(pilihan)

        total = sum(int(rows[i][1]) for i in pilihan)
        total_per_kode.append(total)

    jumlah_spesifikasi = len(kelompok)
    rata_per_kode = [
        total / jumlah_spesifikasi if jumlah_spesifikasi else 0
        for total in total_per_kode
    ]

    # Gambar isi tabel
    y = y_top - tinggi_header

    for i, (label, nilai, style) in enumerate(rows):
        y_next = y - tinggi_baris
        c.line(x0, y_next, kanan, y_next)

        label_lower = label.lower().strip()

        if style == "bold":
            teks_kiri(c, x0 + 4, y_next + 4, label, "Helvetica-Bold", 6.8)
        else:
            font_size = 6.2 if kategori == "Ikan" else 6.4
            lines = bungkus(label, "Helvetica", font_size, spec_w - 12)

            for j, line in enumerate(lines[:2]):
                teks_kiri(
                    c, x0 + 13,
                    y_next + tinggi_baris - 7 - j * 6.5,
                    line, size=font_size
                )

            if nilai:
                teks_tengah(c, (x1+x2)/2, y_next+4, nilai, size=7)

        # Isi total dan rata-rata
        if label_lower.startswith(("total", "rata-rata")):
            for k in range(n_kode):
                cx = x2 + (k + 0.5) * kode_w

                angka = (
                    total_per_kode[k]
                    if label_lower.startswith("total")
                    else rata_per_kode[k]
                )

                teks_tengah(
                    c, cx, y_next + 4,
                    f"{angka:.2f}" if "rata-rata" in label_lower else str(angka),
                    "Helvetica-Bold", 7
                )

        # Centang hanya nilai yang terpilih
        elif nilai and i in pilihan_per_kode[0]:
            pass

        if nilai:
            cy = (y + y_next) / 2

            for k in range(n_kode):
                if i in pilihan_per_kode[k]:
                    cx = x2 + (k + 0.5) * kode_w
                    c.setLineWidth(0.8)
                    c.line(cx-3, cy, cx-1, cy-2)
                    c.line(cx-1, cy-2, cx+3, cy+3)

        y = y_next
    
    # Tabel panelis di bawah tabel skor
    py = y_bottom-43
    pw = 300
    c.rect(kiri+12, py-57, pw, 57)
    c.line(kiri+12, py-15, kiri+12+pw, py-15)
    c.line(kiri+37, py-57, kiri+37, py)
    c.line(kiri+155, py-57, kiri+155, py)
    teks_tengah(c, kiri+24, py-10, "No", "Helvetica-Bold", 7)
    teks_tengah(c, kiri+96, py-10, "Panelis", "Helvetica-Bold", 7)
    teks_tengah(c, kiri+232, py-10, "Tanda Tangan", "Helvetica-Bold", 7)
   
    # Isi panelis
    teks_tengah(c, kiri+24, py-38, "1", "Helvetica", 8)

    teks_tengah(
        c, kiri+96, py-32,
        "Karmi Sasmita, S.Pi",
        "Helvetica-Bold", 7
    )

    teks_tengah(
        c, kiri+96, py-43,
        "NIP. 19820712200312003",
        "Helvetica", 6
    )

    # Gambar tanda tangan
    c.drawImage(
        "ttd.png",
        kiri+175, py-51,
        width=110,
        height=32,
        preserveAspectRatio=True,
        mask="auto")

def buat_pdf(kategori, data, nomor, tanggal):
    if not data:
        raise ValueError(f"Tidak ada media {kategori.lower()} yang dipilih.")

    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    c.setTitle(f"Lembar Penilaian Uji Skor {kategori} Segar")

    judul = (
        "LEMBAR PENILAIAN UJI SKOR IKAN SEGAR"
        if kategori == "Ikan"
        else "LEMBAR PENILAIAN UJI SKOR UDANG SEGAR"
    )

    y = gambar_header(c, judul, nomor, tanggal, data, kategori)
    gambar_tabel_skor(c, y, kategori, len(data))

    c.showPage()
    c.save()
    buffer.seek(0)
    return buffer.getvalue()
# -------------------- UI --------------------

uploaded=st.file_uploader("Upload PDF Permohonan PTK", type=["pdf"])
if uploaded:
    raw=uploaded.getvalue()
    try:
        full=teks_pdf(raw)
        nomor_auto=ambil_nomor(full)
        tanggal_auto=ambil_tanggal(full)
        media_auto=ambil_media(raw)
    except Exception as e:
        st.error(f"PDF tidak dapat dibaca: {e}")
        st.stop()

    a,b=st.columns(2)
    with a:
        nomor = st.text_input(
            "Nomor Permohonan PTK",
            value=nomor_auto
        )
        nomor=st.text_input("Nomor PTK / Kode Aju", value=nomor_auto)
    with b:
        tanggal=st.text_input("Tanggal diterima contoh uji", value=tanggal_auto)

    st.subheader("Media pembawa yang ditemukan")
    if not media_auto:
        st.warning("Nama media pembawa tidak terbaca otomatis. Tambahkan atau isi secara manual di bawah.")
        media_auto=[""]

    data_terpilih=[]
    for i,nama in enumerate(media_auto):
        with st.container(border=True):
            c0,c1,c2,c3,c4,c5=st.columns([0.55,3.0,1.0,1.0,1.1,0.8])
            with c0:
                pilih=st.checkbox("Pilih", value=True, key=f"pilih_{i}", label_visibility="collapsed")
            with c1:
                nama_edit=st.text_input("Media pembawa", value=nama, key=f"nama_{i}")
            with c2:
                jenis=st.selectbox("Formulir", ["Ikan","Udang"], index=0 if jenis_awal(nama)=="Ikan" else 1, key=f"jenis_{i}")
            with c3:
                jumlah=st.text_input("Jumlah", "1", key=f"jumlah_{i}")
            with c4:
                ukuran_default="40-65" if jenis=="Ikan" else "10-15"
                ukuran=st.text_input("Ukuran", ukuran_default, key=f"ukuran_{i}")
            with c5:
                ket=st.text_input("Ket.", "cm", key=f"ket_{i}")
        if pilih and nama_edit.strip():
            data_terpilih.append({"nama":nama_edit.strip(),"kategori":jenis,"jumlah":jumlah.strip() or "1","ukuran":ukuran.strip() or "-","ket":ket.strip() or "-"})

    with st.expander("Tambah media pembawa secara manual"):
        with st.form("manual"):
            nama_manual=st.text_input("Nama media pembawa")
            jenis_manual=st.selectbox("Jenis formulir", ["Ikan","Udang"])
            tambah=st.form_submit_button("Tambahkan")
        if tambah and nama_manual.strip():
            st.session_state.setdefault("manual_items", []).append({"nama":nama_manual.strip(),"kategori":jenis_manual})
            st.rerun()
    for j,item in enumerate(st.session_state.get("manual_items", [])):
        with st.container(border=True):
            m1,m2=st.columns([3,1])
            with m1:
                nm=st.text_input("Nama tambahan", item["nama"], key=f"mn_{j}")
            with m2:
                st.write(item["kategori"])
            data_terpilih.append({"nama":nm,"kategori":item["kategori"],"jumlah":"1","ukuran":"10" if item["kategori"]=="Ikan" else "50-65","ket":"-"})

    if data_terpilih:
        st.subheader("Ringkasan keluaran")
        st.write(f"Total media pembawa dipilih: **{len(data_terpilih)}**")
        for jenis in ["Ikan","Udang"]:
            n=sum(1 for x in data_terpilih if x["kategori"]==jenis)
            if n:
                st.write(f"- Satu formulir {jenis} berisi {n} media pembawa")
        if st.button("Buat PDF", type="primary"):
            try:
                keluaran = {}

                for jenis in ["Ikan", "Udang"]:

                    daftar = [
                        {k: v for k, v in x.items() if k != "kategori"}
                        for x in data_terpilih
                        if x["kategori"] == jenis
                    ]

                    if daftar:
                        kode_aju = buat_kode_aju(nomor, jenis)

                        nama_file = f"Lembar_Uji_Skor_{jenis}.pdf"

                        keluaran[nama_file] = buat_pdf(
                            jenis,
                            daftar,
                            kode_aju,
                            tanggal
                        )

                if not keluaran:
                    st.error("Tidak ada PDF yang berhasil dibuat.")
                else:
                    # SIMPAN SEMUA PDF UNTUK PREVIEW DAN DOWNLOAD
                    st.session_state["preview_pdfs"] = keluaran.copy()

                    if len(keluaran) == 1:
                        nama_file, isi = next(iter(keluaran.items()))

                        st.session_state["hasil_pdf"] = isi
                        st.session_state["nama_pdf"] = nama_file
                        st.session_state["hasil_zip"] = None

                    else:
                        z = io.BytesIO()

                        with zipfile.ZipFile(
                            z, "w", zipfile.ZIP_DEFLATED
                        ) as arsip:

                            for nama_file, isi in keluaran.items():
                                arsip.writestr(nama_file, isi)

                        st.session_state["hasil_zip"] = z.getvalue()
                        st.session_state["hasil_pdf"] = None

                    st.success("PDF berhasil dibuat.")

            except Exception as e:
                st.error(f"Gagal membuat PDF: {e}")

    # ================= PREVIEW DAN DOWNLOAD =================

if st.session_state.get("preview_pdfs"):

    st.divider()
    st.subheader("Preview Lembar Penilaian")

    # Preview dan download masing-masing PDF
    for nama_file, isi_pdf in st.session_state["preview_pdfs"].items():

        st.markdown(f"### {nama_file}")

        # Tombol preview
        if st.button(
            f"Preview {nama_file}",
            key=f"preview_{nama_file}"
        ):
            st.session_state[f"show_{nama_file}"] = not st.session_state.get(
                f"show_{nama_file}", False
            )

        # Tampilkan PDF
        if st.session_state.get(f"show_{nama_file}", False):
            pdf_viewer(isi_pdf)

        # Download PDF masing-masing
        st.download_button(
            label=f"Download {nama_file}",
            data=isi_pdf,
            file_name=nama_file,
            mime="application/pdf",
            key=f"download_{nama_file}"
        )

    # Download semua PDF sekaligus jika ada Ikan dan Udang
    if st.session_state.get("hasil_zip"):

        st.divider()

        st.download_button(
            label="Download Semua PDF (ZIP)",
            data=st.session_state["hasil_zip"],
            file_name="Lembar_Uji_Skor.zip",
            mime="application/zip",
            key="download_semua_zip"
        )
else:
    st.info("Unggah PDF permohonan PTK untuk memulai.")
