# Ekşi Sözlük RSS Servisi

Bu uygulama, Ekşi Sözlük başlıklarını takip etmek için bir RSS servisi sağlar. Belirtilen başlıklardaki yeni girdileri RSS formatında sunar.

## Özellikler

- Ekşi Sözlük başlıklarını RSS formatında takip etme
- Arama terimlerini RSS formatında takip etme
- Tüm takip edilen başlıkları tek bir RSS feed'inde birleştirme
- Basit web arayüzü ile başlık yönetimi
- Önbellek desteği ile performans optimizasyonu

## Kurulum

1. Repo'yu klonlayın:
```
git clone https://github.com/yusufgurdogan/eksi_rss.git
cd eksi_rss
```

2. Sanal ortam oluşturun ve etkinleştirin:
```
python -m venv venv
# Windows için:
venv\Scripts\activate
# Linux/MacOS için:
source venv/bin/activate
```

3. Gerekli paketleri yükleyin:
```
pip install -r requirements.txt
```

4. `.env` dosyasını oluşturun:
```
touch .env
```

5. `.env` dosyasını düzenleyin ve sunucu ayarlarını yapılandırın:
```
HOST=0.0.0.0
PORT=5000
```

## Kullanım

1. Uygulamayı başlatın:
```
python eksi_rss.py
```

2. Tarayıcınızda `http://localhost:5000` adresine gidin.

3. "Yeni Feed Ekle" formunu kullanarak takip etmek istediğiniz başlıkları ekleyin:
   - Başlık URL'si (örn: https://eksisozluk.com/python--109286)
   - Başlık ID'si (örn: 109286)
   - Arama terimi (örn: python programlama)

4. RSS besleme URL'lerini RSS okuyucunuza ekleyin:
   - Belirli bir başlık için: `http://localhost:5000/feed/baslik/{baslik_id}.xml`
   - Tüm başlıklar için: `http://localhost:5000/hepsi.xml`

## Notlar

- Sunucu varsayılan olarak 5000 portunda çalışır, ancak `.env` dosyasında bu değiştirilebilir.
- Abonelikler `abonelikler.json` dosyasında saklanır.
- Ekşi Sözlük'ün kullanım şartlarına uygun şekilde kullanın.
- Çok sayıda istek göndermemeye özen gösterin.

## Lisans

Bu proje MIT lisansı altında lisanslanmıştır. Detaylar için `LICENSE` dosyasına bakın.