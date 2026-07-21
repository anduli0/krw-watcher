"""
Desk structural view — persistent, editable theses the whole committee must weigh.

These are SLOW-MOVING structural priors (months-to-years), distinct from the daily
news scrape. Edit STRUCTURAL_VIEW when a structural shift in Korea's FX supply/demand
is identified (a BOK study, a major exporter's capital-allocation decision, a regime
change in the BoP composition). Every agent and the chief see this block, so keep it
tight, evidence-based, and explicit about WHICH HORIZON it bites (structural → 3m/12m).

Sign reminder: USD/KRW UP = won WEAK = delta_krw > 0.
"""

STRUCTURAL_VIEW = """\
[원화 외환수급의 구조적 견해 — 2026-06 갱신]

핵심 명제: 원화 강세를 만드는 것은 경상수지·투자소득 '흑자 규모'가 아니라, 그 소득이 실제로
국내에 송금·환전되어 외환시장에 달러로 '공급'되는지 여부다. 해외 재투자·현지 유보되면
장부상 흑자라도 외환공급이 아니다 → "경상흑자가 크면 무조건 원화 강세"라는 전통적 앵커는
과거보다 약해졌다. 따라서 헤드라인 흑자를 원화 강세로 곧장 환산하지 말 것.

근거 1 (BOK 이슈노트 2026-15, 2026.6.18):
 · 한국 해외증권투자 2024년 670억$ → 2025년 1,403억$ (GDP대비 3.6%→7.5%) 급증 = 지속적 달러 매입수요.
 · 해외직접투자 소득 중 재투자수익 비중(2010~ 평균): 대만18 < 독일28 < 한국40 < 일본46(%) — 한국도 높아
   투자소득의 상당부분이 국내로 안 들어온다.
 · IMF: 경상수지 중 본원소득수지 비중 2025년 23% → 2030년 42% 전망. 구조가 '상품수지 중심'에서
   '상품+투자소득 동반 흑자'로 이동 중 — 단, 투자소득은 환류돼야만 원화에 도움.
 · 충격반응 크기(앵커): 해외투자 +3% → 원/달러 약 +0.7%(원약세, ≈+10원/스팟1530) / 투자소득 +8% →
   원/달러 약 −0.4%(원강세, ≈−6원, 주로 초기구간). 즉 외환수요(나가는 투자)의 환율 민감도가
   외환공급(들어오는 소득)보다 크다 → net은 구조적 원약세 쪽으로 비대칭.

근거 2 (대형 수출기업의 미국 재투자 — 구조적 원약세):
 · 삼성전자·SK하이닉스 등 경상흑자의 큰 비중을 차지하는 기업들이 미국 현지 팹·AI 투자로 이익
   상당부분을 미국에 재투자할 계획 → 그 흑자가 원화로 환전되지 않음. 경상흑자가 장부상 커져도
   실제 원화 매수 외환공급으로 이어지지 않아 구조적 원약세 압력이 잔존.

[적용 지침]
 · 경상수지·투자소득·BoP를 평가할 때 '흑자 규모'가 아니라 '국내 환류 여부(net FX 공급)'로 한 단계
   더 들어가라. 환류 갭이 크면 흑자에도 불구하고 원화 중립~약세로 본다.
 · 이 견해는 구조 호라이즌(3m·12m)에 더 크게 반영하고, 1w·1m엔 과도 반영 금지(단기는 여전히
   금리차·달러·위안·뉴스 흐름이 지배). 크기는 위 IRF 앵커 수준(분기 한 자릿수~십수 원)으로 절제,
   과장 금지. 이미 알려진 사실이라 시장에 일부 선반영됐을 수 있음도 감안."""


def structural_view_text() -> str:
    """The current desk structural view, injected into every agent + the chief."""
    return STRUCTURAL_VIEW.strip()
