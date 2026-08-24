"""Educational explanations (master spec §7).

Every concept answers the same four questions §7 requires, in Turkish:

    Bu nedir?                  WHAT IS THIS?
    Neden önemli?              WHY DOES IT MATTER?
    Nasıl yorumlanmalı?        HOW SHOULD I INTERPRET IT?
    Ne varsayılmamalı?         WHAT SHOULD I NOT ASSUME?

The fourth is the one that matters most and the one a shorter implementation
would drop. §7 forbids teaching rules like *"RSI > 70 = SELL"*, so every
explanation carries the limitation alongside the reading, and a test asserts
that no explanation contains that shape of advice.

**An explanation is not a claim about the current market.** These are static
definitions; nothing here reads an analysis. `ConceptAvailability` records
whether the project can actually *measure* the concept: `Liquidity` and
`Spread` are in §7's required list but need order-book depth and bid/ask
quotes that this repository does not receive, so they are explained and
explicitly marked `NOT_MEASURED`. A test proves that marker cannot be read as
a live reading.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, unique

from app.application.presentation.terms import TermKey, term


@unique
class Concept(StrEnum):
    """The §7 minimum set, in the order that section lists them."""

    EMA = "EMA"
    SMA = "SMA"
    RSI = "RSI"
    MACD = "MACD"
    ATR = "ATR"
    VWAP = "VWAP"
    ADX = "ADX"
    BOLLINGER_BANDS = "BOLLINGER_BANDS"
    VOLUME = "VOLUME"
    RELATIVE_VOLUME = "RELATIVE_VOLUME"
    OPEN_INTEREST = "OPEN_INTEREST"
    BASIS = "BASIS"
    MARGIN = "MARGIN"
    LEVERAGE = "LEVERAGE"
    RISK_REWARD = "RISK_REWARD"
    SUPPORT = "SUPPORT"
    RESISTANCE = "RESISTANCE"
    BREAKOUT = "BREAKOUT"
    RETEST = "RETEST"
    BOS = "BOS"
    CHOCH = "CHOCH"
    MARKET_REGIME = "MARKET_REGIME"
    LIQUIDITY = "LIQUIDITY"
    SPREAD = "SPREAD"


@unique
class ConceptAvailability(StrEnum):
    """Whether this project can currently measure the concept."""

    MEASURED = "MEASURED"
    """A deterministic engine computes it, so an analysis can report it."""

    NOT_MEASURED = "NOT_MEASURED"
    """Defined here for education only. No data source exists, so no analysis
    reports it, and none may imply that it does."""


@dataclass(frozen=True, slots=True)
class ConceptExplanation:
    """One concept, answering all four §7 questions."""

    concept: Concept
    term_key: TermKey
    what_is_this: str
    why_it_matters: str
    how_to_interpret: str
    what_not_to_assume: str
    availability: ConceptAvailability

    @property
    def turkish_name(self) -> str:
        return term(self.term_key).turkish

    @property
    def bilingual_name(self) -> str:
        return term(self.term_key).bilingual()

    @property
    def is_measured(self) -> bool:
        return self.availability is ConceptAvailability.MEASURED

    @property
    def answers(self) -> tuple[str, str, str, str]:
        """The four answers in §7's order, for a caller that renders them
        uniformly without naming each field."""
        return (
            self.what_is_this,
            self.why_it_matters,
            self.how_to_interpret,
            self.what_not_to_assume,
        )


M = ConceptAvailability.MEASURED
N = ConceptAvailability.NOT_MEASURED

_EXPLANATIONS: dict[Concept, ConceptExplanation] = {
    item.concept: item
    for item in (
        ConceptExplanation(
            Concept.EMA,
            TermKey.EMA,
            "Son fiyatlara daha fazla ağırlık veren bir hareketli ortalamadır.",
            "Fiyatın yönünü, tek tek mumların gürültüsünden arındırarak gösterir.",
            "Fiyat ortalamanın üstündeyse yükseliş, altındaysa düşüş eğilimi "
            "vardır. Kısa ve uzun periyotlu ortalamaların sıralanışı trendin "
            "yönü hakkında fikir verir.",
            "Ortalamalar geçmiş fiyattan hesaplanır; geleceği bilmezler. "
            "Kesişme tek başına al ya da sat işareti değildir ve yatay "
            "piyasada sık sık yanıltır.",
            M,
        ),
        ConceptExplanation(
            Concept.SMA,
            TermKey.SMA,
            "Belirli sayıda mumun kapanış fiyatının düz ortalamasıdır.",
            "Uzun vadeli eğilimi sade biçimde özetler.",
            "Üstel ortalamaya göre daha yavaş tepki verir; bu yüzden uzun "
            "vadeli bakışta daha durağan bir referans sağlar.",
            "Yavaş tepki vermesi onu daha doğru yapmaz, sadece daha geç "
            "yapar. Dönüşleri geriden bildirir.",
            M,
        ),
        ConceptExplanation(
            Concept.RSI,
            TermKey.RSI,
            "Son dönemdeki yükselişlerin düşüşlere oranını 0-100 arasında "
            "ölçen bir momentum göstergesidir.",
            "Hareketin hızının arttığını mı yoksa yavaşladığını mı gösterir.",
            "Yüksek değerler alıcıların baskın olduğunu, düşük değerler "
            "satıcıların baskın olduğunu gösterir. Güçlü bir trendde uzun süre "
            "yüksek ya da düşük kalabilir; bu normaldir.",
            "Yüksek RSI 'sat', düşük RSI 'al' anlamına gelmez. Güçlü bir "
            "yükseliş trendinde gösterge haftalarca yüksek kalabilir ve bu "
            "süre boyunca fiyat yükselmeye devam edebilir. RSI bir olasılık "
            "değil, bir ölçümdür.",
            M,
        ),
        ConceptExplanation(
            Concept.MACD,
            TermKey.MACD,
            "İki hareketli ortalamanın farkını ve bu farkın kendi ortalamasını karşılaştırır.",
            "Momentumun yönünü ve güç değişimini birlikte gösterir.",
            "Histogramın sıfırın üstünde olması yukarı yönlü momentumu, "
            "altında olması aşağı yönlü momentumu gösterir.",
            "Kesişmeler gecikmeli sinyallerdir ve yatay piyasada çok sayıda "
            "yanlış işaret üretir. Tek başına işlem kararı vermez.",
            M,
        ),
        ConceptExplanation(
            Concept.ATR,
            TermKey.ATR,
            "Fiyatın belirli bir dönemde ortalama olarak ne kadar hareket ettiğini ölçer.",
            "Zarar kes seviyesinin ne kadar geniş olması gerektiğini anlamak "
            "için kullanılır; oynaklık arttıkça dar bir stop gürültüye takılır.",
            "Yüksek değer geniş hareket, düşük değer dar hareket demektir. "
            "Yönü değil, büyüklüğü ölçer.",
            "ATR yön göstermez. Yükselen ATR fiyatın yükseleceği anlamına "
            "gelmez; sadece hareketin büyüdüğünü söyler.",
            M,
        ),
        ConceptExplanation(
            Concept.VWAP,
            TermKey.VWAP,
            "İşlem hacmiyle ağırlıklandırılmış ortalama fiyattır.",
            "Gün içinde alıcıların ve satıcıların ortalama maliyetine yakın bir referans verir.",
            "Fiyatın VWAP üstünde olması gün içi alıcıların, altında olması "
            "satıcıların önde olduğunu gösterir.",
            "Bu projede VWAP, doğrulanmış borsa seans saatleri bulunmadığı "
            "için UTC takvim gününe göre hesaplanır. Bu bir geliştirme "
            "varsayılanıdır ve gerçek VİOP seansıyla birebir örtüşmeyebilir.",
            M,
        ),
        ConceptExplanation(
            Concept.ADX,
            TermKey.ADX,
            "Trendin gücünü ölçer; yönünü değil.",
            "Aynı sinyalin güçlü bir trendde mi yoksa yatay bir piyasada mı "
            "oluştuğunu ayırt etmeye yarar.",
            "Yüksek değerler yönü ne olursa olsun güçlü bir trende, düşük "
            "değerler yönsüz bir piyasaya işaret eder.",
            "Yüksek ADX yükseliş demek değildir. Güçlü bir düşüş trendinde de "
            "ADX yüksektir. Yön için başka bir ölçüme bakmak gerekir.",
            M,
        ),
        ConceptExplanation(
            Concept.BOLLINGER_BANDS,
            TermKey.BOLLINGER_BANDS,
            "Bir ortalamanın etrafına, fiyatın kendi oynaklığına göre "
            "genişleyip daralan bantlar çizer.",
            "Fiyatın kendi son dönemine kıyasla ne kadar uçta olduğunu gösterir.",
            "Bantların daralması oynaklığın azaldığını, genişlemesi arttığını "
            "gösterir. Fiyatın banda değmesi, hareketin o dönem için uçta "
            "olduğu anlamına gelir.",
            "Üst banda değmek satış, alt banda değmek alış işareti değildir. "
            "Güçlü trendlerde fiyat bant boyunca yürüyebilir.",
            M,
        ),
        ConceptExplanation(
            Concept.VOLUME,
            TermKey.VOLUME,
            "Belirli bir sürede el değiştiren sözleşme miktarıdır.",
            "Bir hareketin arkasında gerçek katılım olup olmadığını gösterir.",
            "Yüksek hacimle gelen hareket daha fazla katılımcının fikrini "
            "yansıtır; düşük hacimli hareket daha az bilgi taşır.",
            "Hacim yön göstermez ve yüksek hacim hareketin devam edeceğini "
            "garanti etmez. Hacim verisi yoksa bu 'hacim düşük' demek "
            "değildir; ölçüm yapılamamış demektir.",
            M,
        ),
        ConceptExplanation(
            Concept.RELATIVE_VOLUME,
            TermKey.RELATIVE_VOLUME,
            "Güncel hacmin, aynı enstrümanın son dönemdeki ortalama hacmine oranıdır.",
            "Hacmin mutlak büyüklüğü enstrümandan enstrümana değişir; oran "
            "bunu karşılaştırılabilir hale getirir.",
            "1'in üzerindeki değerler olağandan yoğun, altındaki değerler "
            "olağandan sakin bir katılıma işaret eder.",
            "Yüksek göreceli hacim hareketin doğru yönde olduğunu göstermez; "
            "sadece olağandan fazla katılım olduğunu söyler.",
            M,
        ),
        ConceptExplanation(
            Concept.OPEN_INTEREST,
            TermKey.OPEN_INTEREST,
            "Kapatılmamış vadeli sözleşme sayısıdır.",
            "Piyasaya yeni para mı giriyor yoksa mevcut pozisyonlar mı "
            "kapanıyor sorusuna bağlam sağlar.",
            "Fiyat ve açık pozisyon değişiminin birlikte okunması gerekir; "
            "tek başına açık pozisyon bir yön bildirmez.",
            "Açık pozisyon okumaları bu projede bilinçli olarak yönsüzdür. "
            "Artan açık pozisyon 'al', azalan 'sat' anlamına gelmez.",
            M,
        ),
        ConceptExplanation(
            Concept.BASIS,
            TermKey.BASIS,
            "Vadeli sözleşme fiyatı ile dayanak varlığın spot fiyatı arasındaki farktır.",
            "Vadeli fiyatın spota göre primli mi iskontolu mu işlem gördüğünü gösterir.",
            "Pozitif baz vadelinin spottan pahalı, negatif baz ucuz olduğunu "
            "gösterir. Bu bir bağlam bilgisidir.",
            "Prim 'yükselecek', iskonto 'düşecek' anlamına gelmez. Baz bu "
            "projede yön üretmez; sadece bağlam sunar.",
            M,
        ),
        ConceptExplanation(
            Concept.MARGIN,
            TermKey.MARGIN,
            "Bir vadeli pozisyon açmak için hesapta bloke edilen tutardır.",
            "Hesabın kaç sözleşme taşıyabileceğini belirler.",
            "Teminat, pozisyonun kontrol ettiği toplam büyüklüğün küçük bir "
            "kısmıdır. Serbest teminat azaldıkça yeni pozisyon kapasitesi azalır.",
            "Teminat azami zarar değildir. Vadeli bir pozisyon, yatırılan "
            "teminattan daha fazla zarar ettirebilir.",
            M,
        ),
        ConceptExplanation(
            Concept.LEVERAGE,
            TermKey.LEVERAGE,
            "Pozisyonun kontrol ettiği büyüklüğün hesap büyüklüğüne oranıdır.",
            "Aynı fiyat hareketinin hesaba kaç kat yansıyacağını belirler.",
            "Kaldıraç arttıkça hem kâr hem zarar aynı oranda büyür. Küçük bir "
            "fiyat hareketi hesapta büyük bir değişim yaratabilir.",
            "Kaldıraç kazanç aracı değil, büyütme aracıdır. Yüksek kaldıraç "
            "kazanma ihtimalini artırmaz; kaybın hızını artırır.",
            M,
        ),
        ConceptExplanation(
            Concept.RISK_REWARD,
            TermKey.RISK_REWARD,
            "Hedefe olan mesafenin, zarar kes seviyesine olan mesafeye oranıdır.",
            "Bir işlemin geometrisinin makul olup olmadığını gösterir.",
            "Oranın yüksek olması, aynı riske karşılık daha fazla potansiyel "
            "getiri olduğunu gösterir.",
            "Bu oran bir başarı olasılığı değildir ve kurulum kalitesiyle "
            "aynı şey değildir. Yüksek oran, hedefe ulaşılacağı anlamına gelmez.",
            M,
        ),
        ConceptExplanation(
            Concept.SUPPORT,
            TermKey.SUPPORT,
            "Fiyatın daha önce birden fazla kez tepki verdiği, altta kalan bölgedir.",
            "Düşüşlerin yavaşlayabileceği alanları önceden görmeyi sağlar.",
            "Bir seviye değil bir bölgedir. Ne kadar çok dokunulmuşsa o kadar dikkate değerdir.",
            "Destek fiyatın oradan döneceğini garanti etmez. Destekler "
            "kırılır; bu normal bir piyasa davranışıdır.",
            M,
        ),
        ConceptExplanation(
            Concept.RESISTANCE,
            TermKey.RESISTANCE,
            "Fiyatın daha önce birden fazla kez geri döndüğü, üstte kalan bölgedir.",
            "Yükselişlerin zorlanabileceği alanları önceden görmeyi sağlar.",
            "Destek gibi bir bölgedir; kesin bir fiyat değildir.",
            "Direnç fiyatın oradan döneceğini garanti etmez ve dirence yakın "
            "olmak 'sat' demek değildir.",
            M,
        ),
        ConceptExplanation(
            Concept.BREAKOUT,
            TermKey.BREAKOUT,
            "Fiyatın bir destek ya da direnç bölgesinin dışına çıkmasıdır.",
            "Bir bölgenin artık geçerli olmayabileceğine dair ilk işarettir.",
            "Kırılımın teyit edilmesi gerekir; bölgenin dışına çıkan her hareket kalıcı olmaz.",
            "Kırılım anında girmek çoğu zaman en kötü fiyattır. Teyit "
            "edilmemiş bir kırılım henüz bir olgu değildir.",
            M,
        ),
        ConceptExplanation(
            Concept.RETEST,
            TermKey.RETEST,
            "Kırılan bölgeye fiyatın geri dönüp o bölgeyi sınamasıdır.",
            "Kırılımın gerçek olup olmadığını gösteren en somut kanıtlardan biridir.",
            "Bölge yeni yönde tutarsa kırılım güç kazanır; tutmazsa kırılım başarısız sayılır.",
            "Her kırılımın yeniden testi olmaz ve yeniden test başarılı olsa "
            "bile hareketin devam edeceği kesin değildir.",
            M,
        ),
        ConceptExplanation(
            Concept.BOS,
            TermKey.BOS,
            "Piyasanın mevcut yönünü sürdürerek son önemli tepe ya da dibi geçmesidir.",
            "Mevcut yapının devam ettiğini gösterir.",
            "Yükselişte önceki tepenin, düşüşte önceki dibin geçilmesi yapının "
            "sürdüğü anlamına gelir.",
            "Yapı kırılımı bir al-sat emri değildir. Yapının devam ettiğini "
            "söyler, hareketin nereye kadar gideceğini değil.",
            M,
        ),
        ConceptExplanation(
            Concept.CHOCH,
            TermKey.CHOCH,
            "Piyasanın yönünü değiştirdiğine dair ilk yapısal işarettir.",
            "Mevcut yönün zayıfladığını erken gösterir.",
            "Yükseliş yapısında son dibin kırılması ya da düşüş yapısında son "
            "tepenin aşılması karakter değişimi sayılır.",
            "Karakter değişimi trendin tamamen döndüğünü kanıtlamaz. Alt "
            "zaman dilimlerinde her geri çekilmede görülebilir.",
            M,
        ),
        ConceptExplanation(
            Concept.MARKET_REGIME,
            TermKey.MARKET_REGIME,
            "Piyasanın hangi tür davranış içinde olduğunun sınıflandırmasıdır: "
            "trend, yatay bant, kırılım ya da belirsiz.",
            "Aynı gösterge farklı rejimlerde farklı anlamlar taşır.",
            "Trend rejiminde yön takip edilir, yatay bantta bölge sınırları önem kazanır.",
            "Rejim bir tahmin değildir; şu ana kadarki davranışın "
            "sınıflandırmasıdır ve değişebilir. 'Belirsiz' sonucu bir hata "
            "değil, geçerli bir cevaptır.",
            M,
        ),
        ConceptExplanation(
            Concept.LIQUIDITY,
            TermKey.LIQUIDITY,
            "Bir enstrümanda, fiyatı fazla hareket ettirmeden işlem yapılabilme kolaylığıdır.",
            "Düşük likiditede giriş ve çıkış maliyeti beklenenden yüksek olur.",
            "Derin bir emir defteri ve dar bir makas yüksek likiditeye işaret eder.",
            "Bu projede likidite ölçülmemektedir. Emir defteri derinliği "
            "verisi alınmadığı için hiçbir analiz likidite hakkında bir şey "
            "söylemez; buradaki açıklama yalnızca eğitim amaçlıdır.",
            N,
        ),
        ConceptExplanation(
            Concept.SPREAD,
            TermKey.SPREAD,
            "En iyi alış ile en iyi satış fiyatı arasındaki farktır.",
            "Bir işleme girer girmez oluşan gizli maliyettir.",
            "Dar makas düşük işlem maliyeti, geniş makas yüksek maliyet anlamına gelir.",
            "Bu projede makas ölçülmemektedir. Alış-satış kotasyonu "
            "alınmadığı için hiçbir analiz makas hakkında bir şey söylemez; "
            "buradaki açıklama yalnızca eğitim amaçlıdır.",
            N,
        ),
    )
}


def explain(concept: Concept) -> ConceptExplanation:
    """The explanation for ``concept``.

    Every member of `Concept` has one; a test asserts the registry covers the
    §7 list completely, so this cannot raise in practice.
    """
    return _EXPLANATIONS[concept]


def all_concepts() -> tuple[ConceptExplanation, ...]:
    """Every explanation, in §7's declaration order."""
    return tuple(_EXPLANATIONS[concept] for concept in Concept)


def measured_concepts() -> tuple[ConceptExplanation, ...]:
    """Concepts an analysis can actually report on."""
    return tuple(item for item in all_concepts() if item.is_measured)


def unmeasured_concepts() -> tuple[ConceptExplanation, ...]:
    """Concepts defined for education only, with no data source."""
    return tuple(item for item in all_concepts() if not item.is_measured)
