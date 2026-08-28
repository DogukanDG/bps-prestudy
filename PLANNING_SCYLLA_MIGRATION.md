# Scylla Migration Plan — `bps-prestudy`

**Amaç:** `bps-prestudy` duyarlılık analizi hattında simülasyon motorunu Prosimos'tan Scylla'ya değiştirmek (veya her ikisini seçilebilir hale getirmek), sonuçların karşılaştırılabilirliğini koruyarak.

**Referans commit:** `249e19e` — Add pre-study script and two discovered models

---

## 0. Referans depolar ve dosya haritası

Bu plan sıfırdan bir tasarım değil. Mimari referansı **SimuBridge**, doğruluk referansı ise Prosimos / pix-framework / Scylla kaynak kodudur. Uygulamaya başlamadan önce bu depolar yerel olarak klonlanmalı ve aşağıdaki dosyalar okunmalıdır.

### Klonlanacaklar

```bash
# Mimari referans — Simod ile Scylla'yı birbirine bağlayan tek açık kaynak örnek
git clone https://github.com/INSM-TUM/SimuBridge--Main.git
git -C SimuBridge--Main checkout 31489618d44a2acfebd4c3af080c5360ccc50d92

# Ana depo — güncel sürüm, SOPA uzantısında Scylla plugin örnekleri var
git clone --recurse-submodules https://github.com/INSM-TUM/SimuBridge.git

# Hedef simülatör — XML şeması, parser davranışı, örnek konfigürasyonlar
git clone https://github.com/bptlab/scylla.git

# Kaynak simülatör — KPI tanımları ve dağılım semantiği
git clone https://github.com/AutomatedProcessImprovement/Prosimos.git
```

Ayrıca `pix-framework`'ün **kullandığın Simod sürümünün pyproject.toml'unda yazan** versiyonu:

```bash
pip download pix-framework==<sürüm> --no-deps -d ./pixref
```

### SimuBridge — nereye bakılacak

`SimuBridge--Main` deposunda, üç npm paketi var. Her birinden ne alınacağı farklı:

| Dosya | Ne için okunacak | Durum |
|---|---|---|
| `dataModel/DataModel.js` | Pivot ara temsil. `distributionTypes` listesinin **Scylla'nın desteklediği küme** olduğuna dikkat — tasarımın çekirdek fikri bu. | ✅ Mimari olarak benimse |
| `scyllaConverter/GlobConfig.js` | GC XML'inin yapısı: `<resourceData>`, `<dynamicResource>`, `<instance>`, `<timetables>` | ✅ Yapıyı referans al |
| `scyllaConverter/SimConfig.js` | SC XML'inin yapısı: `startEvent`, `task`, gateway'ler | ✅ Yapıyı referans al |
| `scyllaConverter/ConvertElements.js` | Eleman üretimi; özellikle gateway tag adının BPMN'deki `$type`'tan türetilmesi | ✅ Tekniği al |
| `simodConverter/simod_converter.js` → `makeDistribution()` | **Dağılım parametre eşlemesi** | ❌ **Kopyalama.** Okuduğu indeksler Prosimos'un yazdığı indekslerle uyuşmuyor (bkz. §2 B3). Sadece "ne yapılmaması gerektiği" örneği olarak oku. |
| `simodConverter/simod_converter.js` → `getTaskDuration()` | `resources[0]` kısayolu ve içindeki yorum | ❌ Kopyalama; B1'in nasıl kaçınıldığını görmek için oku |
| `simodConverter/simod_converter.js` → `timeToNumber()` | Takvim saatlerinin tam saate yuvarlanması | ❌ Kopyalama |
| `frontend/src/components/Processminer/simod_config.yml` | Simod'un hangi ayarlarla koşulduğu (`discovery_type: pool`, `granularity: 60`) | ✅ Faz 0 Karar 1 için önemli emsal |

### Scylla — nereye bakılacak

| Yol | Ne için |
|---|---|
| `samples/` | **Gerçek, çalışan GC/SC dosyaları.** XML şablonunun tek güvenilir kaynağı. `bsim` namespace'i buradan al, elle uydurma. |
| `src/main/java/de/hpi/bpt/scylla/parser/` | Parser'ın hangi tag ve attribute'ları gerçekten okuduğu. Wiki eksik olabilir, kod eksik olamaz. |
| `src/main/java/de/hpi/bpt/scylla/Scylla.java` | CLI arayüzü (`--headless`, `--config`, çoklu `--bpmn` / `--sim`) — Faz 4'teki toplu koşum stratejisi buna dayanıyor |
| `src/main/java/de/hpi/bpt/scylla/plugin/` | Mevcut plugin'ler; `is_arrival_calendar` için plugin yazma kararı verilirse emsal |
| Git log: `Fix #72` (2024-02) | Named resource instance takvimlerinin yok sayılması hatası. **Eski Scylla sürümü kullanma.** |

### Prosimos / pix-framework — doğruluk referansı

| Yol | Ne için |
|---|---|
| `pix_framework/statistics/distribution.py` → `to_prosimos_distribution()` | **Dağılım parametre sırasının tek doğru kaynağı.** Faz 2.1'deki tablo buradan doğrulanmalı, dokümantasyondan değil. |
| `prosimos/simulation_stats_calculator.py` | `idle_cycle_time`, `idle_processing_time`, `idle_time` metriklerinin tam tanımları. Faz 2.5'te bunlar XES üzerinde yeniden implemente edilecek. |
| `prosimos/simulation_engine.py` | Varış takviminin nasıl uygulandığı — E1 ablasyonunun ne ölçtüğünü anlamak için |

### Uyarı

SimuBridge'in mimarisi sağlam, dağılım katmanı değil. Bu ayrımı korumak bu planın en kritik noktası: **yapıyı taklit et, sayıları kendi doğrulanmış eşlemenle üret.**

---

## 1. Mevcut mimari ve kesim noktası

Hat şu anda şöyle akıyor:

```
Simod → parameters.json
  → extract_parameters/     (JSON'dan parametre tabloları)
  → sampling/               (Sobol / Morris tasarım matrisi)
  → convert_samples/        (uniform örnekler → alan değerleri)
  → write_converted_samples (örnek başına tam JSON, chunk dosyalarına)
  → simulation/simulate_samples.py   ← MOTOR BURADA
  → merge_parquet_chunks    (KPI ortalamaları)
  → sensitivity_analysis/   (Sobol / Morris indeksleri)
```

Motor bağımlılığı tek bir yerde toplanmış:

```python
from prosimos.simulation_engine import run_simulation as _simulate_samples
```

`simulate_sample()` fonksiyonu bir örneği geçici JSON'a yazıyor, Prosimos'u **kütüphane olarak** çağırıyor, dönen KPI nesnelerini satır listelerine çeviriyor.

**Kesim noktası: `simulate_sample()` fonksiyonunun imzası.** Girdi bir örnek sözlüğü, çıktı sabit şemalı satır listeleri. Bu sözleşme korunursa hattın geri kalanı (`write_dataframe_chunk`, `merge_parquet_chunks`, tüm `sensitivity_analysis/` modülü, frontend) hiç değişmez.

**Tasarım ilkesi:** Motor değişikliği bu fonksiyonun *arkasında* kalmalı. `process_rows` şeması aynen korunacak:
`{sample_id, metric, min, max, avg, total, count}` — metric ∈ {cycle_time, processing_time, waiting_time, idle_cycle_time, idle_processing_time, idle_time}.

---

## 2. Engeller

### B1 — Differentiated kaynak modeli (KRİTİK, yapısal)

Her iki modelde de her `resource_profile` tek kaynak içeriyor (Production: 48/48, BPIC 2012: 47/47) ve `task_resource_distribution` her (task, kaynak) çifti için ayrı dağılım tutuyor. Bir task'ta 22 farklı kaynak, 22 farklı dağılım var.

Scylla'da süre `<task><duration>` altında, yani **kaynağa bağlı değil**. Ayrıca `<resources>` içindeki `<resource>` elemanları alternatif değil, hepsi birden gerekli. 22 profil doğrudan yazılırsa task "22 kaynağın hepsi aynı anda müsait olsun" anlamına gelir ve süreç kilitlenir.

Çözüm seçenekleri Faz 0'da.

### B2 — İki SA boyutu temsil edilemiyor (KRİTİK, metodolojik)

| SA boyutu | Scylla'da karşılık | Durum |
|---|---|---|
| `is_gateway` | `<exclusiveGateway><outgoingSequenceFlow><branchingProbability>` | ✅ |
| `is_arrival_distribution` | `<startEvent><arrivalRate>` | ⚠️ dağılım ailesi kısıtlı |
| `is_arrival_calendar` | **yok** | ❌ |
| `is_tasks_resources` | `<task><duration>` | ❌ parametre uzayı çöküyor |
| `is_resource_calendars` | `<timetable>` / `<instance timetableId>` | ✅ |
| `is_resource_numbers` | `defaultQuantity` | ✅ |

`is_arrival_calendar` için tek gerçek çözüm Scylla'ya start event'e timetable bağlayan bir plugin yazmak. `is_tasks_resources` için parametre tanımı değişmek zorunda — bu, Prosimos sonuçlarıyla doğrudan karşılaştırılamaz hale gelmek demek.

### B3 — Dağılım ailesi (YÜKSEK, düzeltilebilir)

Scylla yalnızca şunları destekliyor: `binomial, constant, erlang, exponential, triangular, normal, poisson, uniform, arbitraryFiniteProbabilityDistribution`.

Modellerdeki dağılım dağılımı:

| | Production | BPIC 2012 |
|---|---|---|
| lognorm | 14 | 70 |
| gamma | 32 | 48 |
| expon | 22 | 42 |
| fix | 54 | 15 |
| norm | 10 | 8 |
| uniform | 14 | 8 |

BPIC 2012'de süre dağılımlarının **%62'si lognorm veya gamma**, yani Scylla'da doğrudan karşılığı olmayan aile. Naif moment matching bu modellerde kuyruk davranışını yok eder.

### B4 — KPI hesaplama (ORTA)

Prosimos bellekte KPI nesnesi döndürüyor. Scylla XES log + kaynak kullanım raporu yazıyor. `cycle_time`, `processing_time`, `waiting_time` XES'ten yeniden hesaplanabilir, ancak `idle_cycle_time`, `idle_processing_time`, `idle_time` Prosimos'a özgü takvim-farkında metrikler; tanımlarının birebir yeniden implemente edilmesi gerekiyor.

### B5 — Verim (ORTA, çözülebilir)

Prosimos in-process çağrılıyor, `joblib` ile `n_jobs=-5` paralelleştirme var. Scylla bir JVM süreci. ~178.500 koşuda süreç başına 1–2 sn JVM açılışı tek başına 50–100 saat eder.

Scylla CLI tek çağrıda birden fazla `--bpmn` ve `--sim` alabiliyor, ama **tek `--config`**. Yani:
- `is_gateway` / `is_arrival_distribution` / `is_tasks_resources` SA'sı → GC sabit → tek JVM'de yüzlerce örnek toplu koşabilir ✅
- `is_resource_calendars` / `is_resource_numbers` SA'sı → örnek başına farklı GC → toplu koşamaz ❌ → sıcak JVM harness'ı şart

### B6 — Tekrarlanabilirlik (DÜŞÜK, kolay)

GC'ye `randomSeed` ve `zoneOffset` yazılmazsa aynı model iki farklı sonuç verir. Duyarlılık analizinde bu kabul edilemez.

---

## 3. Faz 0 — Kapsam kararı (kod yazmadan önce)

Bu faz mühendislik değil, tez metodolojisi kararı. Süpervizörle (Samira) konuşulmadan Faz 1'e geçilmemeli.

**Ama kararlar tahminle değil ölçümle verilmeli.** Faz 0.1 bunu sağlıyor: B1 ve B2'nin etkisi, tek satır Scylla kodu yazmadan, mevcut Prosimos hattı içinde ölçülebilir.

---

### Faz 0.1 — Ablasyon deneyleri (ÖNCE BU)

Scylla'ya geçince kaybedeceğin iki şeyi Prosimos içinde yapay olarak kaldırıp etkiyi ölçüyoruz. Scylla'nın vereceği sonuca yakınsayan bir üst sınır elde ediyoruz.

#### Neden mümkün

Her iki kayıp da girdi JSON'unda taklit edilebilir:
- **Varış takvimi kaybı** = `arrival_time_calendar`'ı haftanın tamamıyla değiştirmek
- **Kaynak farklılaşması kaybı** = `task_resource_distribution`'ı task başına tek dağılıma indirmek + `resource_profiles`'ı tek havuza toplamak

#### Modellerdeki başlangıç durumu

Takvim kapsama oranları (hesaplandı):

| Model | Varış takvimi | Kaynak takvimi (ort.) |
|---|---|---|
| Production | 57 h/hafta (%34) | 22 h/hafta (%13) |
| BPIC 2012 | 75 h/hafta (%45) | 37 h/hafta (%22) |
| BPIC 2017 | 83 h/hafta (%49) | 35 h/hafta (%21) |
| DataMining | 102 h/hafta (%61) | 10 h/hafta (%6) |

Aynı task'ı yapan kaynakların heterojenliği:

| Model | Medyan (en yavaş / en hızlı) | Medyan CV |
|---|---|---|
| Production | 10.6× | 0.63 |
| BPIC 2012 | **655×** | **3.70** |

BPIC 2012'de bir task'ta 42 kaynak var, ortalamaları 0.3 dk ile 420 dk arasında değişiyor.

#### Deneyler

| # | Ne değiştirilir | Neyi ölçer |
|---|---|---|
| **E1** | `arrival_time_calendar` → `[{from: MONDAY, to: SUNDAY, beginTime: 00:00:00, endTime: 23:59:59}]` | Varış takvimi kaybının tek başına etkisi |
| **E2** | Task başına kaynak dağılımları tek karışıma indirilir; `resource_profiles` tek havuza toplanır (quantity = N) | Kaynak farklılaşması kaybının tek başına etkisi |
| **E3** | E1 + E2 birlikte | Bileşik etki — Scylla'nın vereceği sonucun üst sınır tahmini |

Her deney için: taban modelde ve **tam SA koşusunda**. Sadece taban KPI'ları karşılaştırmak yeterli değil; asıl soru SA sonuçlarının değişip değişmediği.

#### Ölçülecekler

**Seviye 1 — KPI kayması:** `cycle_time`, `waiting_time`, `processing_time` için ortalama ve dağılım. `cases_list`'teki her değer için ayrı — sapmanın vaka sayısıyla nasıl büyüdüğü kritik.

**Seviye 2 — SA sonucu:** Sobol / Morris indeksleri. İki soru:
- Parametrelerin **sıralaması** korunuyor mu? (Spearman rank korelasyonu)
- İndekslerin mutlak değerleri ne kadar kayıyor?

#### Ön tahminler (bunlar test edilecek hipotezler, sonuç değil)

- **E1**: Aynı N vaka takvim süresine ~3 kat yoğun girer, kaynak kapasitesi sabit kalır → kuyruk şişer → **bekleme ve cycle time yukarı sapar**. Processing time değişmez. Sapma sabit bir kayma değil; kuyruk doğrusal olmadığı için vaka sayısıyla birlikte büyür. 1000 vakada sistemin doyuma gitmesi mümkün.
- **E2**: Prosimos'ta hızlı kaynaklar işi çabuk bitirip tekrar müsait olduğu için orantısız çok iş alır; sistemin efektif ortalaması kaynakların düz ortalamasından düşüktür. Ağırlıksız agregasyon **süreleri fazla tahmin eder**. Ayrıca cycle time dağılımının kuyruğu daralır.
- **SA üzerinde**: `is_resource_calendars` ve `is_resource_numbers` duyarlılığı **abartılır** (sistem doyuma yakınken kapasiteye aşırı hassas olur). `is_gateway` duyarlılığı **bastırılır** (baskın kuyruk etkisinin altında kalır). `is_arrival_distribution` abartılır.

#### Karar kuralı

| Sonuç | Anlamı | Ne yapılır |
|---|---|---|
| KPI sapması küçük, sıralama korunuyor | Çeviri kaybı tolere edilebilir | Plana devam, Faz 1 |
| KPI sapması büyük, **sıralama korunuyor** | Metodoloji motordan bağımsız — güçlü bulgu | Plana devam; bu bulgu tezin ana katkılarından biri olur |
| **Sıralama da değişiyor** | Scylla kolunun metodolojik değeri sorgulanır | Faz 0 Karar 3'ü (b) yönünde netleştir veya kapsamı daralt |

Üçüncü durumda bile bu deney kazanç: aynı sonucu 10 hafta kod yazdıktan sonra öğrenmemiş olursun.

#### Uygulama notları

- Mevcut hattı kullan; yeni motor yok. `run_prestudy.py` üzerinden, girdi JSON'u ön işleyen küçük bir script yeterli.
- E2'deki karışım: her kaynağın dağılımından `assignedTasks` yüküne göre ağırlıklı örnek çek, birleşik örneklemden tek dağılım üret. Ağırlıksız versiyonu da koş — aradaki fark, ağırlıklandırmanın önemini gösterir ve Faz 2'deki agregasyon stratejisini belirler.
- Maliyet düşük tutmak için önce `cases_list=[100]` ve küçük örnek sayısıyla koş; sinyal varsa büyüt.
- Çıktı: `docs/ablation_results.md` — tablolar ve sıralama korelasyonları.

**Çıkış kriteri:** Üç deneyin SA sonuçları elde edilmiş, karar kuralı uygulanmış.

**Tahmini süre:** 2–3 gün geliştirme + koşu süresi.

---

### Karar 1: Kaynak modeli

| Seçenek | Ne yapılır | Artı | Eksi |
|---|---|---|---|
| **A — Havuzlu yeniden keşif** | Simod `resource_profiles.discovery_type: pool` ile yeniden koşulur, ayrı bir model seti üretilir | Scylla'ya temiz oturur; çeviri neredeyse kayıpsız | Modeller Eksi tezindeki modellerden farklı olur; replikasyon iddiası zayıflar |
| **B — Karışım agregasyonu** | Differentiated model korunur; task başına kaynakların dağılımları yük ağırlıklı karışıma indirgenir, tüm kaynaklar tek `dynamicResource` (quantity = N) olur | Aynı modeller kullanılır | Kaynak farklılaşması kaybolur; `is_tasks_resources` SA'sı anlamını yitirir |
| **C — İki kollu tasarım** | A ve B'nin ikisi de üretilir, fark ölçülür | En savunulabilir bilimsel sonuç | En yüksek maliyet |

> **Öneri:** C, ama B'yi önce yap. B zaten A'nın alt kümesi olan kodu gerektiriyor; A sadece farklı bir Simod konfigürasyonu.
>
> **E2 sonucu bu kararı doğrudan besliyor:** agregasyon kaybı küçükse B tek başına yeter, büyükse A veya C gerekir.

### Karar 2: SA boyutları

Hangi boyutlar Scylla kolunda koşulacak:

- [ ] `is_gateway` — koş
- [ ] `is_arrival_distribution` — koş (dağılım eşleme kalitesi raporlanarak)
- [ ] `is_arrival_calendar` — **karar gerekli**: (i) kapsam dışı bırak ve gerekçelendir, (ii) Scylla plugin'i yaz. **E1'in büyüklüğü belirleyici:** etki büyükse plugin yazmak yatırıma değer, küçükse kapsam dışı bırakmak savunulabilir.
- [ ] `is_tasks_resources` — **karar gerekli**: parametre tanımı task seviyesine indirilerek koşulsun mu, yoksa kapsam dışı mı. **E2 belirleyici.**
- [ ] `is_resource_calendars` — koş
- [ ] `is_resource_numbers` — koş

### Karar 3: Karşılaştırmanın anlamı

Bu çalışmanın iddiası ne:
- (a) "Aynı BPS modelinde iki motor aynı SA sonucunu veriyor mu?" → motor karşılaştırması, çeviri sadakati kritik
- (b) "Scylla ile de bu metodoloji uygulanabilir mi?" → uygulanabilirlik çalışması, çeviri kayıpları raporlanabilir sınırlamalar

(a) çok daha zor ve B1/B2 yüzünden tam olarak mümkün değil. (b) daha gerçekçi ve dürüst.

**Faz 0 çıktısı:** `docs/ablation_results.md` (E1–E3 ölçümleri) + `docs/scope_decisions.md` (kararlar, ablasyon sonuçlarına atıfla gerekçelendirilmiş), süpervizör onaylı.

**Sıra:** Faz 0.1 (ölçüm) → Karar 1–3 → Faz 1. Faz 1 spike'ı Faz 0.1 ile paralel yürütülebilir, çünkü birbirine bağlı değiller.

---

## 4. Faz 1 — Spike (elle uçtan uca bir koşu)

Kod yazmadan önce Scylla'nın gerçekten ne yaptığını görmek. Otomasyon yok, elle.

1. Scylla'yı `main` branch'inden derle veya son release zip'ini indir. **Eski sürüm kullanma** — 2024'teki `Fix #72` (named resource instance default timetables ignored) bizim kullanacağımız `<instance>` mekanizmasını etkiliyor.
2. `backend/models/production/` altındaki BPMN + JSON'dan **elle** bir `global_config.xml` ve `sim_config.xml` yaz. Şablon: Scylla repo'sundaki `samples/Kreditkarte_global_1.xml` ve `samples/Kreditkarte_sim_1.xml`. Namespace `bsim` kullan.
3. Headless koş:
   ```
   java -jar scylla.jar --headless --config=global_config.xml \
        --bpmn=Production_train.bpmn --sim=sim_config.xml --output=out/
   ```
4. Üretilen XES logunu ve rapor dosyalarını incele. Hangi metrikler hazır geliyor, hangileri hesaplanmalı?
5. BPMN uyumluluğunu doğrula: Simod'un ürettiği BPMN Split Miner çıktısı; inclusive gateway'ler ve `replace_or_joins` sonucu oluşan yapılar Scylla parser'ından geçiyor mu?

**Çıkış kriteri:** Bir modelin bir konfigürasyonu Scylla'da hatasız koşuyor ve XES log üretiyor. Sayıların doğruluğu bu fazda önemli değil.

**Tahmini süre:** 2–3 gün.

---

## 5. Faz 2 — Adapter katmanı

Mevcut paket yapısını taklit eden yeni bir alt paket. Hiçbir mevcut dosya silinmiyor, `simulate_samples.py` bir `engine` parametresi kazanıyor.

```
backend/src/simulation_pipeline/simulation/
├── simulate_samples.py          # engine="prosimos" | "scylla" dispatch
├── prosimos/
│   └── run_sample.py            # mevcut simulate_sample() buraya taşınır
└── scylla/
    ├── distributions.py         # Prosimos dağılımı → Scylla dağılımı
    ├── build_global_config.py   # JSON → GC XML
    ├── build_sim_config.py      # JSON + BPMN → SC XML
    ├── run_scylla.py            # JVM çağrısı / harness istemcisi
    ├── parse_results.py         # XES + rapor → KPI satırları
    └── kpi_definitions.py       # idle_* metriklerinin yeniden implementasyonu
```

### 2.1 `distributions.py`

Tek sorumluluğu: bir Prosimos dağılım sözlüğünü Scylla XML dağılım öğesine çevirmek. Tüm çeviri hatalarının izole edildiği yer.

**Parametre sırası** (`pix_framework/statistics/distribution.py::to_prosimos_distribution` — kullandığın Simod sürümünde teyit et, dokümantasyona güvenme):

| Simod | `distribution_params` sırası | Scylla hedefi |
|---|---|---|
| `fix` | `[value]` | `constantDistribution(constantValue=value)` |
| `expon` | `[mean, min, max]` | `exponentialDistribution(mean=p0)` |
| `norm` | `[mean, std, min, max]` | `normalDistribution(mean=p0, standardDeviation=p1)` |
| `uniform` | `[min, max]` | `uniformDistribution(lower=p0, upper=p1)` |
| `lognorm` | `[mean, var, min, max]` | diskretizasyon (aşağıda) |
| `gamma` | `[mean, var, min, max]` | diskretizasyon (aşağıda) |

**lognorm / gamma için:** `arbitraryFiniteProbabilityDistribution` kullan. Dağılımdan N örnek çek (`min`/`max` sınırlarına göre kırparak), K kovaya histogram çıkar, kova merkezlerini `<entry value=... frequency=.../>` olarak yaz. K bir konfigürasyon parametresi olsun (varsayılan 100) ve duyarlılık analizinin kendisinde de test edilebilsin.

Moment matching (`normal(mean, √var)`) sadece geri düşüş seçeneği olarak kalsın, varsayılan olmasın — bu modellerde kuyruk uzun.

**Truncation:** Prosimos `min`/`max` dışındaki örnekleri yeniden çekiyor. Diskretizasyon bunu doğal olarak koruyor. Diğer ailelerde negatif süre üretilmediğini test et.

**Birim:** Prosimos'ta her şey saniye. Tüm `timeUnit` attribute'ları `SECONDS`.

### 2.2 `build_global_config.py`

GC'ye giren: kaynaklar, takvimler, seed, saat dilimi.

- `resource_calendars[].time_periods` → `<timetable><timetableItem from to beginTime endTime/>` — alan isimleri neredeyse birebir, saat yuvarlaması **yapma** (`granule_size` zaten 60 dk, ama modelde 06:00–07:00 gibi tam saatler var; yine de yuvarlama yapan kod yazma).
- `resource_profiles` → Faz 0 Karar 1'e göre:
  - Seçenek A: profil başına bir `<dynamicResource defaultQuantity=N>`
  - Seçenek B: task başına ilgili kaynakları tek `<dynamicResource>` altında topla, `defaultQuantity` = kaynak sayısı, kaynak bazlı takvimleri `<instance timetableId=...>` ile koru
- `<randomSeed>` ve `<zoneOffset>` **mutlaka** yaz. Seed = SA seed'inden deterministik olarak türetilsin (örn. `hash(sample_id, run_idx)`), ki replication run'lar farklı ama tekrarlanabilir olsun.

### 2.3 `build_sim_config.py`

- `processRef` = BPMN'deki process id (bpmn-moddle yerine Python tarafında `lxml` ile oku)
- `processInstances` = `total_cases` — **SimuBridge'deki 5000 kırpmasını taşıma**, gerçek değeri yaz ve Scylla'nın limitini ampirik olarak test et
- `startDateTime` = `start_iso` (`simulate_samples`'daki `2023-01-01T00:00:00+02:00`)
- `<startEvent><arrivalRate>` = `arrival_time_distribution`
- `<task><duration>` + `<resources>` = `task_resource_distribution` (Karar 1'e göre agrege)
- `<exclusiveGateway>` / `<inclusiveGateway>` = `gateway_branching_probabilities`; tag adı gateway tipine göre değişiyor, BPMN'den tipi oku

### 2.4 `run_scylla.py`

İki mod:

**Mod 1 — toplu (GC sabit olduğunda):** Bir chunk'taki tüm örnekler için tek GC + N adet SC yaz, tek çağrı:
```
java -jar scylla.jar --headless --config=gc.xml --bpmn=model.bpmn \
     --sim=sc_0.xml --sim=sc_1.xml ... --output=out/
```
`is_gateway`, `is_arrival_distribution`, `is_tasks_resources` boyutlarında geçerli.

**Mod 2 — sıcak JVM harness (GC değiştiğinde):** Küçük bir Java sınıfı, stdin'den (GC yolu, SC yolu, çıktı yolu) satırları okuyup `SimulationManager`'ı çağırsın, süreç açık kalsın. Python tarafı bu süreçle boru üzerinden konuşsun. `is_resource_calendars` ve `is_resource_numbers` boyutlarında şart.

Mod 1 ile başla, Mod 2'yi Faz 5'te ekle.

### 2.5 `parse_results.py` + `kpi_definitions.py`

XES logundan `process_rows` şemasını üret. `cycle_time` / `processing_time` / `waiting_time` doğrudan hesaplanabilir.

`idle_*` metrikleri için Prosimos'un tanımlarını kaynak koddan çıkar (`prosimos/simulation_stats_calculator.py`) ve aynısını XES üzerinde implemente et. Bu ayrı bir doğrulama gerektiriyor: aynı log üzerinde Prosimos'un hesabıyla senin hesabın örtüşmeli.

**Çıkış kriteri:** `engine="scylla"` ile hat baştan sona koşuyor, `process_kpis_*.parquet` dosyaları Prosimos kolundakiyle aynı şemada üretiliyor.

**Tahmini süre:** 3–4 hafta.

---

## 6. Faz 3 — Doğrulama

Sırayla, her biri bir öncekine bağlı:

**T1 — Determinizm testi.** Tüm dağılımları `fix`/`constant`'a çevir, gateway olasılıklarını 0/1 yap, tek kaynak, 7/24 takvim. İki motor **birebir aynı** cycle time vermeli. Vermiyorsa çeviri hatası var, motor farkı değil.

**T2 — Analitik test.** Tek aktivite, sabit süre, sonsuz kaynak, bilinen varış oranı. Cycle time elle hesaplanabilir. Her iki motor da doğru sayıyı vermeli.

**T3 — Dağılım sadakati.** Her dağılım ailesi için: Prosimos'tan 10.000 örnek, Scylla çevirisinden 10.000 örnek, Wasserstein mesafesi. Eşik belirle ve raporla. lognorm/gamma için diskretizasyon kova sayısının etkisini burada ölç.

**T4 — Takvim testi.** Tek kaynak, dar takvim (Pzt–Cum 09:00–17:00), yoğun varış. Kaynak kullanımı ve bekleme süreleri iki motorda tutuyor mu? Varış takvimi farkını izole etmek için Prosimos'u önce 7/24 varış takvimiyle koş, sonra gerçek takvimle — aradaki fark B2'nin büyüklüğünü verir.

**T5 — Tam model testi.** Production ve BPIC 2012 modelleri, taban konfigürasyon, 100/500/1000 vaka. Cycle time dağılımlarını karşılaştır. Sapmayı üç kaynağa ayrıştır: varış takvimi, dağılım ailesi, kaynak agregasyonu.

**Çıktı:** `docs/translation_fidelity.md` — her Prosimos özelliğinin Scylla'daki temsili, kayıp varsa büyüklüğü. Tezde doğrudan kullanılabilir bir tablo.

**Tahmini süre:** 1.5–2 hafta.

---

## 7. Faz 4 — Ölçeklendirme

1. Sıcak JVM harness'ı (Mod 2) implemente et.
2. Tek örnek için duvar saati süresini ölç, 178.500 koşuya ekstrapole et. Prosimos'la oranı çıkar.
3. `server_computing/slurm/run_prestudy.sh`'yi Java'lı ortam için uyarla (JDK modülü, heap ayarı `-Xmx`, `n_jobs` yerine JVM sayısı).
4. Bellek: Scylla tüm event listesini bellekte tutuyor; 1000 vaka × büyük model için heap ihtiyacını ölç.
5. Disk: XES logları CSV'den çok daha büyük. Log yazımını kapatıp sadece KPI toplayabiliyor musun kontrol et — `xeslogger` plugin'i devre dışı bırakılabilir.

**Tahmini süre:** 1 hafta.

---

## 8. Faz 5 — Karşılaştırma çalışması

1. Her iki motorda aynı SA konfigürasyonlarını koş (Faz 0 Karar 2'de seçilen boyutlar).
2. Sobol / Morris indekslerini karşılaştır: parametrelerin **sıralaması** aynı mı? Mutlak değerler farklı olsa bile sıralama korunuyorsa metodoloji motordan bağımsız demektir — bu güçlü bir bulgu.
3. Ayrıştır: sıralama farkı nereden geliyor — çeviri kaybından mı, motor semantiğinden mi?

---

## 9. Risk kaydı

| Risk | Etki | Önlem |
|---|---|---|
| Simod BPMN'i Scylla parser'ından geçmiyor | Faz 1'de bloke | Faz 1'in ilk işi bu; geçmezse BPMN sanitizasyon adımı gerekir |
| `idle_*` metriklerini yeniden üretemiyorsun | KPI şeması eksik kalır | Bu metrikleri Scylla kolunda `None` bırakıp SA'yı `cycle_time` üzerinden yap; kararı erken ver |
| Scylla 1000 vaka × büyük modelde çöküyor / çok yavaş | Ölçek bloke | Faz 1'de tek koşuyla ölç, erken gör |
| Diskretizasyon SA sonuçlarını kendisi etkiliyor | Bulgular kirlenir | T3'te kova sayısını değiştirerek etkiyi ölç ve raporla |
| Faz 0 kararları geç alınıyor | Boşa kod yazılır | Faz 1 spike'ından sonra, Faz 2'den önce mutlaka kapat |
| Çeviri kayıpları SA sıralamasını bozuyor — geç fark ediliyor | 10 haftalık iş boşa gider | **Faz 0.1 ablasyon deneyleri** bunu ilk 3 günde ortaya çıkarır |

---

## 10. Kabul kriterleri

- [ ] `simulate_samples(engine="prosimos")` mevcut davranışı birebir koruyor (regresyon testi)
- [ ] `simulate_samples(engine="scylla")` aynı şemada `process_rows` üretiyor
- [ ] T1 determinizm testi geçiyor
- [ ] T3'te her dağılım ailesi için sadakat metriği ölçülmüş ve belgelenmiş
- [ ] GC'de `randomSeed` ve `zoneOffset` yazılıyor; aynı seed iki kez aynı sonucu veriyor
- [ ] `docs/ablation_results.md`, `docs/translation_fidelity.md` ve `docs/scope_decisions.md` mevcut
- [ ] Faz 5'teki gerçek Scylla sapması, Faz 0.1'deki E3 tahminiyle karşılaştırılmış (tahmin tuttu mu?)
- [ ] `sensitivity_analysis/` ve frontend kodunda **hiçbir değişiklik yok**

---

## 11. Kapsam dışı

- SimuBridge'in `simod_converter.js` dosyasını yeniden kullanmak. Mimarisi (pivot data model) alınabilir, kodu alınamaz — dağılım parametre eşlemesi yazıldığı günden beri hatalı.
- Scylla'yı Prosimos'un tüm özelliklerini destekleyecek şekilde genişletmek (batching, case attributes, prioritisation).
- `is_arrival_calendar` için Scylla plugin'i yazmak — Faz 0'da açıkça karar verilmedikçe.

---

## 12. Kaba zaman çizelgesi

| Faz | Süre | Bağımlılık |
|---|---|---|
| **0.1 — Ablasyon deneyleri** | **2–3 gün + koşu** | **— (ilk iş)** |
| 0 — Kapsam kararı | 1 hafta (çoğu bekleme) | Faz 0.1 |
| 1 — Spike | 2–3 gün | — (Faz 0 ile paralel yürüyebilir) |
| 2 — Adapter | 3–4 hafta | Faz 0 + Faz 1 |
| 3 — Doğrulama | 1.5–2 hafta | Faz 2 |
| 4 — Ölçeklendirme | 1 hafta | Faz 3 |
| 5 — Karşılaştırma | 2 hafta | Faz 4 |

Toplam yaklaşık 9–11 hafta tam zamanlı eşdeğeri. HiWi temposunda bunu 2–3 katına çıkarmak gerçekçi.

---

## 13. Bilimsel dayanaklar

Her tasarım kararının nereye dayandığı. Bu tablo tezin ilgili bölümüne doğrudan taşınabilir.

| Karar | Dayanak |
|---|---|
| Pivot ara temsil + GC/SC ayrımı | Bein et al., *SimuBridge: Discovery and Management of Process Simulation Scenarios*, BPM 2023 Demos/Resources Forum, CEUR-WS Vol-3469 |
| Scylla'nın konfigürasyon şeması ve eklenti mimarisi | Pufahl, Wong, Weske, *Design of an Extensible BPMN Process Simulator*, BPM Workshops 2018 |
| Simod ile model keşfi | Camargo, Dumas, González-Rojas, *Automated discovery of business process simulation models from event logs*, DSS 134 (2020) |
| E2 ablasyonu (havuzlanmış ↔ farklılaşmış kaynak) | López-Pintado & Dumas, *Business Process Simulation with Differentiated Resources: Does it Make a Difference?*, BPM 2022 — havuzlanmış/farklılaşmış konfigürasyonları karşılaştıran deney tasarımı |
| Duyarlılık analizi metodolojisi | Ugur Eksi tezi (replike edilen çalışma) |
| Dağılım parametre eşlemesi | `pix_framework/statistics/distribution.py` kaynak kodu — birincil kaynak |
| Scylla XML şeması | `bptlab/scylla` `samples/` ve `parser/` kaynak kodu — birincil kaynak |

### Atıf ve dürüstlük notları

- SimuBridge **mimari referans**, metodolojik referans değil. Tezde şu şekilde ifade edilmeli: *"SimuBridge'in Simod–Scylla köprüleme mimarisini referans aldık; dağılım eşleme katmanını kullandığımız Simod sürümünün parametre şemasına göre yeniden implemente ettik."*
- SimuBridge'in dağılım eşlemesindeki tutarsızlık, üst geliştirici gruba bildirilmeli (GitHub issue). Grup TUM bünyesinde ve Scylla'nın yazarı Luise Pufahl da SimuBridge yazarları arasında — danışma kanalı mevcut.
- Literatürde emsali olmayan kararlar (ör. diskretizasyon kova sayısı, KPI yeniden tanımlamaları) `docs/scope_decisions.md` içinde gerekçesi ve reddedilen alternatifiyle birlikte yazılmalı. Bilimsel gereklilik her kararın kaynaklı olması değil, her kararın **gerekçeli ve tekrarlanabilir** olmasıdır.
