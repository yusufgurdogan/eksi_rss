from flask import Flask, Response, render_template, request, redirect, url_for
import cloudscraper
from bs4 import BeautifulSoup
from feedgen.feed import FeedGenerator
import datetime
import pytz
import re
import time
import os
import json
import logging
from urllib.parse import quote
from flask_caching import Cache
from dotenv import load_dotenv

# .env dosyasını yükle
load_dotenv()

# Günlük kaydını yapılandır
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

uygulama = Flask(__name__)

# Önbelleği yapılandır
onbellek_ayarlari = {
    "DEBUG": True,
    "CACHE_TYPE": "SimpleCache",
    "CACHE_DEFAULT_TIMEOUT": 900  # 15 dakika
}
uygulama.config.from_mapping(onbellek_ayarlari)
onbellek = Cache(uygulama)

# Abone olunan başlıklar için veri dosyası
ABONELIKLER_DOSYASI = 'abonelikler.json'

def abonelikleri_yukle():
    """Abone olunan başlıkların listesini dosyadan yükle"""
    if os.path.exists(ABONELIKLER_DOSYASI):
        with open(ABONELIKLER_DOSYASI, 'r', encoding='utf-8') as f:
            return json.load(f)
    return []

def abonelikleri_kaydet(abonelikler):
    """Abone olunan başlıkların listesini dosyaya kaydet"""
    with open(ABONELIKLER_DOSYASI, 'w', encoding='utf-8') as f:
        json.dump(abonelikler, f, ensure_ascii=False, indent=2)

def baslik_url_ayrıstir(girdi):
    """Farklı giriş formatlarını ayrıştırarak geçerli bir Ekşi Sözlük başlık URL'si ve ID'si al"""
    # Tam URL ise
    if girdi.startswith('http'):
        # Varsa başlık ID'sini çıkar
        eslesme = re.search(r'--(\d+)', girdi)
        if eslesme:
            baslik_id = eslesme.group(1)
            return girdi, baslik_id
        return girdi, None
    
    # Sadece sayısal ID ise
    if girdi.isdigit():
        return f"https://eksisozluk.com/baslik/{girdi}", girdi
    
    # ID'li bir slug ise
    eslesme = re.search(r'--(\d+)', girdi)
    if eslesme:
        baslik_id = eslesme.group(1)
        return f"https://eksisozluk.com/{girdi}", baslik_id
    
    # Diğer durumlarda, arama terimi olduğunu varsay
    kodlanmis_terim = quote(girdi)
    return f"https://eksisozluk.com/?q={kodlanmis_terim}", None

@onbellek.memoize(timeout=900)  # 15 dakika için önbellekte tut
def eksi_sayfasi_al(url):
    """Cloudscraper ile bir Ekşi Sözlük sayfasını al"""
    scraper = cloudscraper.create_scraper(
        browser={
            'browser': 'chrome',
            'platform': 'windows',
            'desktop': True
        },
        delay=5
    )
    
    try:
        logger.info(f"URL alınıyor: {url}")
        # Yeni bir oturum oluştur ve sayfayı al
        yanit = scraper.get(url, allow_redirects=True)
        yanit.raise_for_status()  # 4XX/5XX yanıtları için bir istisna fırlatır
        
        # Yanıtı döndür
        return yanit
    except Exception as e:
        logger.error(f"Sayfa alınırken hata oluştu {url}: {e}")
        # Çağıranın işleyebilmesi için istisnayı yeniden fırlat
        raise

def baslik_bilgisi_al(url, baslik_id=None):
    """Başlık bilgisini al ve yönlendirmeleri işle"""
    yanit = eksi_sayfasi_al(url)
    if not yanit:
        return None, None, None, None
    
    # Yönlendirme olduysa, URL'yi güncelle
    son_url = yanit.url
    logger.info(f"Yönlendirmeden sonraki son URL: {son_url}")
    
    # HTML'i ayrıştır
    soup = BeautifulSoup(yanit.text, 'html.parser')
    
    # Başlık bilgisini çıkar
    baslik_elemani = soup.select_one('h1#title')
    if not baslik_elemani:
        logger.error("Başlık bulunamadı - sayfa yapısı değişmiş olabilir")
        return None, None, None, None
    
    baslik_metni = baslik_elemani.get_text(strip=True)
    
    # Başlık ID'si sağlanmadıysa, çıkarmaya çalış
    if not baslik_id:
        baslik_id = baslik_elemani.get('data-id')
        if not baslik_id:
            # URL'den çıkarmaya çalış
            eslesme = re.search(r'--(\d+)', son_url)
            if eslesme:
                baslik_id = eslesme.group(1)
    
    # Başlık slug'ını çıkar
    baslik_slug = None
    if "data-slug" in baslik_elemani.attrs:
        baslik_slug = baslik_elemani.get('data-slug')
    else:
        # URL'den çıkarmaya çalış
        eslesme = re.search(r'/(.*?)--\d+', son_url)
        if eslesme:
            baslik_slug = eslesme.group(1)
    
    logger.info(f"Başlık bulundu: '{baslik_metni}' (ID: {baslik_id})")
    return baslik_metni, baslik_id, baslik_slug, son_url

def baslik_icin_feed_olustur(baslik_url, baslik_id=None, max_sayfa=3):
    """Ekşi Sözlük başlığındaki girdiler için sayfalama ve doğru saat dilimi ile RSS feed'i oluştur"""
    # Öncelikle tarih parametresi olmadan başlık bilgisini al
    baslik_metni, baslik_id, baslik_slug, son_url = baslik_bilgisi_al(baslik_url, baslik_id)
    
    if not baslik_metni or not baslik_id:
        logger.error(f"{baslik_url} için başlık bilgisi alınamadı")
        return None
    
    # Feed oluştur
    fg = FeedGenerator()
    fg.id(son_url)
    fg.title(f'Ekşi Sözlük - {baslik_metni}')
    fg.link(href=son_url, rel='alternate')
    fg.description(f'Başlık için yeni girdiler: {baslik_metni}')
    fg.language('tr')
    
    # Feed seviyesi yayın tarihi ekle (Türkiye saat dilimi ile)
    istanbul_saat_dilimi = pytz.timezone('Europe/Istanbul')
    fg.pubDate(datetime.datetime.now(istanbul_saat_dilimi))
    
    # URL'ye bugünün tarih parametresini ekle
    bugun = datetime.datetime.now().strftime('%Y-%m-%d')
    
    # Birden fazla sayfayı işle
    eklenen_girdi_sayisi = 0
    
    for sayfa in range(1, max_sayfa + 1):
        # Tarih ve sayfa parametreleriyle URL oluştur
        if '?' in son_url:
            sayfa_url = f"{son_url}&day={bugun}"
        else:
            sayfa_url = f"{son_url}?day={bugun}"
            
        # İlk sayfa değilse sayfa parametresini ekle
        if sayfa > 1:
            sayfa_url = f"{sayfa_url}&p={sayfa}"
        
        logger.info(f"Sayfa {sayfa} tarih parametresiyle alınıyor: {sayfa_url}")
        
        # Sayfayı al
        try:
            yanit = eksi_sayfasi_al(sayfa_url)
            if not yanit:
                break
        except Exception as e:
            logger.warning(f"Bugünün tarihi ({bugun}) için girdi bulunamadı: {e}")
            
            # Geri dönmek yerine, özel bir "bugün girdi yok" girdisi ekle
            fe = fg.add_entry()
            fe.id(f"{son_url}#bugun-girdi-yok-{bugun}")
            fe.title("Bilgilendirme")
            fe.link(href=son_url)
            fe.author(name="Ekşi RSS")
            fe.content(f"Bugün ({bugun}) için bu başlıkta herhangi bir entry bulunmamaktadır.", type='html')
            fe.published(datetime.datetime.now(istanbul_saat_dilimi))
            
            # Sadece bilgi girdisiyle feed'i döndür
            return fg
        
        # HTML'i ayrıştır
        soup = BeautifulSoup(yanit.text, 'html.parser')
        
        # Girdileri çıkar
        girdiler = soup.select('ul#entry-item-list > li')
        if not girdiler:
            # Başka girdi bulunamadı, sayfalamayı durdur
            if sayfa == 1:
                # Özel bir "bugün girdi yok" girdisi ekle
                fe = fg.add_entry()
                fe.id(f"{son_url}#bugun-girdi-yok-{bugun}")
                fe.title("Bilgilendirme")
                fe.link(href=son_url)
                fe.author(name="Ekşi RSS")
                fe.content(f"Bugün ({bugun}) için bu başlıkta herhangi bir entry bulunmamaktadır.", type='html')
                fe.published(datetime.datetime.now(istanbul_saat_dilimi))
            break
            
        logger.info(f"Sayfa {sayfa}'da {len(girdiler)} girdi bulundu, başlık: {baslik_metni}")
        
        # Girdileri işle
        for girdi in girdiler:
            try:
                girdi_id = girdi.get('data-id')
                if not girdi_id:
                    continue
                    
                yazar = girdi.get('data-author')
                icerik_elemani = girdi.select_one('div.content')
                if not icerik_elemani:
                    continue
                    
                icerik = icerik_elemani.decode_contents()
                tarih_elemani = girdi.select_one('div.info a.entry-date')
                if not tarih_elemani:
                    continue
                    
                tarih_metni = tarih_elemani.get_text(strip=True)
                kalici_baglanti = tarih_elemani.get('href')
                
                # Feed'de girdi oluştur
                fe = fg.add_entry()
                fe.id(f'https://eksisozluk.com{kalici_baglanti}')
                
                # Başlık olarak sadece yazar adını kullan
                fe.title(yazar)
                
                fe.link(href=f'https://eksisozluk.com{kalici_baglanti}')
                fe.author(name=yazar)
                fe.content(icerik, type='html')
                
                # Tarihi ayrıştır ve Türkiye saat dilimi ile saat dilimi bilgisini ekle
                tarih_eslesme = re.search(r'(\d{2}\.\d{2}\.\d{4} \d{2}:\d{2})', tarih_metni)
                if tarih_eslesme:
                    tarih_str = tarih_eslesme.group(1)
                    try:
                        # Tarihi ayrıştır ve Türkiye saat dilimi ile saat dilimi bilgisini ekle
                        girdi_tarihi = datetime.datetime.strptime(tarih_str, '%d.%m.%Y %H:%M')
                        girdi_tarihi = istanbul_saat_dilimi.localize(girdi_tarihi)
                        fe.published(girdi_tarihi)
                    except ValueError as e:
                        logger.warning(f"Tarih ayrıştırma hatası '{tarih_str}': {e}")
                        # Türkiye saat dilimi ile şu anki zamanı kullan
                        fe.published(datetime.datetime.now(istanbul_saat_dilimi))
                else:
                    # Türkiye saat dilimi ile şu anki zamanı kullan
                    fe.published(datetime.datetime.now(istanbul_saat_dilimi))
                
                eklenen_girdi_sayisi += 1
            except Exception as e:
                logger.error(f"Girdi işlenirken hata oluştu: {e}")
                
        # Sonraki sayfaya devam edip etmememiz gerektiğini kontrol et
        # Bu sayfada beklenenden daha az girdi varsa, daha fazla sayfa alma
        if len(girdiler) < 10:  # Her sayfada yaklaşık 10 girdi olduğunu varsayarak
            break
    
    logger.info(f"Toplam {eklenen_girdi_sayisi} girdi eklendi, başlık: {baslik_metni}")
    return fg

@uygulama.route('/')
def anasayfa():
    """Mevcut feed'leri gösteren ana sayfa"""
    abonelikler = abonelikleri_yukle()
    return render_template('index.html', abonelikler=abonelikler)

@uygulama.route('/feed_ekle', methods=['POST'])
def feed_ekle():
    """Yeni bir başlık feed'i ekle"""
    baslik_girdi = request.form.get('baslik', '')
    if not baslik_girdi:
        return redirect(url_for('anasayfa'))
    
    # Başlık URL/tanımlayıcısını ayrıştır
    baslik_url, baslik_id = baslik_url_ayrıstir(baslik_girdi)
    
    # Başlık bilgisini al
    baslik_metni, baslik_id, baslik_slug, son_url = baslik_bilgisi_al(baslik_url, baslik_id)
    
    if not baslik_id or not baslik_metni:
        return render_template('error.html', mesaj=f"Başlık bulunamadı: {baslik_girdi}")
    
    # Zaten yoksa aboneliklere ekle
    abonelikler = abonelikleri_yukle()
    for abone in abonelikler:
        if abone.get('id') == baslik_id:
            return redirect(url_for('anasayfa'))
    
    abonelikler.append({
        'id': baslik_id,
        'baslik': baslik_metni,
        'url': son_url,
        'slug': baslik_slug,
        'ekleme_tarihi': datetime.datetime.now().isoformat()
    })
    
    abonelikleri_kaydet(abonelikler)
    return redirect(url_for('anasayfa'))

@uygulama.route('/feed_kaldir/<baslik_id>')
def feed_kaldir(baslik_id):
    """Bir başlık feed'ini kaldır"""
    abonelikler = abonelikleri_yukle()
    abonelikler = [abone for abone in abonelikler if abone.get('id') != baslik_id]
    abonelikleri_kaydet(abonelikler)
    return redirect(url_for('anasayfa'))

@uygulama.route('/feed/baslik/<baslik_id>.xml')
def id_ile_feed(baslik_id):
    """ID ile belirli bir başlık için RSS feed'i sunar"""
    # Bunun bilinen bir abonelik olup olmadığını kontrol et
    abonelikler = abonelikleri_yukle()
    baslik_url = None
    
    for abone in abonelikler:
        if abone.get('id') == baslik_id:
            baslik_url = abone.get('url')
            break
    
    if not baslik_url:
        baslik_url = f"https://eksisozluk.com/baslik/{baslik_id}"
    
    # Sayfalama ile feed oluştur (en fazla 3 sayfa al)
    fg = baslik_icin_feed_olustur(baslik_url, baslik_id, max_sayfa=3)
    
    if not fg:
        return "Feed oluşturulamadı", 500
    
    # RSS oluştur
    rss_feed = fg.rss_str(pretty=True)
    
    return Response(rss_feed, mimetype='application/xml')

@uygulama.route('/feed/arama/<path:arama_terimi>.xml')
def arama_ile_feed(arama_terimi):
    """Belirli bir arama terimi için RSS feed'i sunar"""
    kodlanmis_terim = quote(arama_terimi)
    baslik_url = f"https://eksisozluk.com/?q={kodlanmis_terim}"
    
    # Feed oluştur
    fg = baslik_icin_feed_olustur(baslik_url)
    
    if not fg:
        return "Feed oluşturulamadı", 500
    
    # RSS oluştur
    rss_feed = fg.rss_str(pretty=True)
    
    return Response(rss_feed, mimetype='application/xml')

@uygulama.route('/hepsi.xml')
def tum_feedler():
    """Tüm abone olunan başlıkların birleştirilmiş feed'ini sunar"""
    abonelikler = abonelikleri_yukle()
    
    # Birleştirilmiş feed oluştur
    fg = FeedGenerator()
    fg.id(request.url)
    fg.title('Ekşi - Tüm Abone Olunan Başlıklar')
    fg.link(href=request.url, rel='self')
    fg.description('Tüm abone olunan Ekşi Sözlük başlıklarının birleştirilmiş feed\'i')
    fg.language('tr')
    
    # Feed seviyesi yayın tarihi ekle (saat dilimi ile)
    fg.pubDate(datetime.datetime.now(pytz.UTC))
    
    # Performans için en son 10 başlıkla sınırla
    for abone in abonelikler[:10]:
        baslik_id = abone.get('id')
        baslik_url = abone.get('url')
        
        if baslik_url:
            baslik_feed = baslik_icin_feed_olustur(baslik_url, baslik_id)
            if baslik_feed:
                # Bu feed'den girdileri birleştirilmiş feed'e ekle
                for girdi in baslik_feed.entry():
                    fg.add_entry(girdi)
    
    # RSS oluştur
    rss_feed = fg.rss_str(pretty=True)
    
    return Response(rss_feed, mimetype='application/xml')

# Şablon dosyalarını oluştur
def sablon_dosyalari_olustur():
    if not os.path.exists('templates'):
        os.makedirs('templates')
        
    index_sablonu = '''
    <!DOCTYPE html>
    <html>
    <head>
        <title>Ekşi Sözlük RSS Servisi</title>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <style>
            body { font-family: Arial, sans-serif; max-width: 800px; margin: 0 auto; padding: 20px; }
            h1 { color: #76a912; }
            .feed-list { margin-top: 20px; }
            .feed-item { padding: 10px; border-bottom: 1px solid #eee; }
            .feed-title { font-weight: bold; }
            .feed-url { color: #666; font-size: 0.9em; word-break: break-all; }
            .feed-actions { margin-top: 5px; }
            .feed-actions a { color: #76a912; text-decoration: none; }
            form { margin-top: 20px; padding: 15px; background: #f5f5f5; border-radius: 5px; }
            input[type="text"] { width: 70%; padding: 8px; }
            button { padding: 8px 15px; background: #76a912; color: white; border: none; cursor: pointer; }
            .combined-feed { margin-top: 20px; padding: 10px; background: #f0f9e8; border-radius: 5px; }
        </style>
    </head>
    <body>
        <h1>Ekşi Sözlük RSS Servisi</h1>
        
        <form action="/feed_ekle" method="post">
            <h3>Yeni Feed Ekle</h3>
            <input type="text" name="baslik" placeholder="Başlık URL'si, ID'si veya arama terimi" required>
            <button type="submit">Ekle</button>
        </form>
        
        <div class="combined-feed">
            <h3>Birleştirilmiş Feed</h3>
            <p>Tüm başlıkları tek bir feed'de takip et: <a href="/hepsi.xml">/hepsi.xml</a></p>
        </div>
        
        <div class="feed-list">
            <h3>Abone Olunan Başlıklar</h3>
            {% if abonelikler %}
                {% for abone in abonelikler %}
                    <div class="feed-item">
                        <div class="feed-title">{{ abone.baslik }}</div>
                        <div class="feed-url">{{ abone.url }}</div>
                        <div class="feed-actions">
                            <a href="/feed/baslik/{{ abone.id }}.xml">RSS Görüntüle</a> | 
                            <a href="/feed_kaldir/{{ abone.id }}">Kaldır</a>
                        </div>
                    </div>
                {% endfor %}
            {% else %}
                <p>Henüz abonelik yok. Bazı başlıklar ekleyin!</p>
            {% endif %}
        </div>
    </body>
    </html>
    '''
    
    hata_sablonu = '''
    <!DOCTYPE html>
    <html>
    <head>
        <title>Hata - Ekşi Sözlük RSS Servisi</title>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <style>
            body { font-family: Arial, sans-serif; max-width: 800px; margin: 0 auto; padding: 20px; }
            h1 { color: #d9534f; }
            .error-box { padding: 15px; background: #f2dede; border: 1px solid #ebccd1; color: #a94442; border-radius: 5px; }
            a { color: #76a912; text-decoration: none; }
        </style>
    </head>
    <body>
        <h1>Hata</h1>
        <div class="error-box">
            <p>{{ mesaj }}</p>
        </div>
        <p><a href="/">Ana sayfaya dön</a></p>
    </body>
    </html>
    '''
    
    # Şablon dosyalarını yaz
    with open(os.path.join('templates', 'index.html'), 'w', encoding='utf-8') as f:
        f.write(index_sablonu)
    
    with open(os.path.join('templates', 'error.html'), 'w', encoding='utf-8') as f:
        f.write(hata_sablonu)

if __name__ == '__main__':
    # .env dosyasından host ve port değerlerini al, varsayılan değerler sağla
    host = os.getenv('HOST', '0.0.0.0')
    port = int(os.getenv('PORT', 5000))
    
    # Abonelik dosyası yoksa oluştur
    if not os.path.exists(ABONELIKLER_DOSYASI):
        abonelikleri_kaydet([])
    
    # Şablon dosyalarını oluştur
    sablon_dosyalari_olustur()
    
    # Uygulamayı çalıştır
    uygulama.run(host=host, port=port, debug=False)