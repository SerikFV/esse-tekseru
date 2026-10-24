"""Run against the REAL Gemini service and PostgreSQL; never substitutes fixtures."""
import argparse
import sys

from fastapi.testclient import TestClient

from backend.config import Settings
from backend.main import create_app

CASES = {
    "қалыпты": ("Оқудың маңызы", "Кітап оқу адамның сыни ойлауын дамытады. Менің ойымша, күнделікті оқу мектепте қолдау табуы тиіс.\nБіріншіден, оқушы түрлі кейіпкердің шешімін салыстырып, өз пікірін дәлелдеуді үйренеді. Мысалы, бір оқиғаны екі кейіпкердің көзқарасымен талқылау біржақты тұжырымнан сақтайды.\nАлайда кітап оқу тәжірибені алмастырмайды. Сондықтан оқылған ойды өмірдегі тапсырмамен байланыстыру қажет.\nДемек, оқу мен тәжірибені ұштастыру білімді тереңдетеді."),
    "құрылымы толық емес": ("Мектеп", "Мектепте жоба жасау маңызды, себебі оқушы өз шешімін тәжірибеде тексереді. Мысалы, өсімдік өсіргенде су мен жарықтың әсерін салыстырады."),
    "аргументі жеткіліксіз": ("Білім", "Білім өте маңызды. Барлығы білім алуы керек. Менің ойымша, білім жақсы. Қорытындылай келе, білім қажет."),
    "дәлелсіз": ("Технология", "Технология оқу сапасын жақсартады, себебі ақпаратқа жол ашады. Ол уақытты үнемдейді және оқушыны ынталандырады. Сондықтан оны сабақта қолдану қажет."),
    "қорытындысыз": ("Көлік", "Қалада қоғамдық көлікті дамыту маңызды. Себебі ол бір мезетте көп адамды тасиды. Мысалы, бір автобус ондаған жеке автокөліктің сапарын алмастыра алады. Алайда бағыттар қолайлы болмаса, тұрғындар оны таңдамайды."),
    "грамматикалық қателер": ("Кітап", "Мен кітап оқиды. Балалар кітапты жақсы көремін себебі ол бізге білім береді Кітаптар пайдалысы көп. Сондықтан біз оқиды керек."),
    "ғылыми": ("Өсімдік өсуі", "Өсімдіктің өсуі жарыққа тәуелді. Мұны салыстырмалы тәжірибе арқылы бағалауға болады. Бірдей топырақтағы екі өсімдіктің суару мөлшерін тұрақты ұстап, жарық уақытын өзгерту ұсынылады. Өсім биіктігі күн сайын өлшенеді. Басқа факторлар бақыланбаса, айырманы тек жарықпен түсіндіру қате болады. Осылайша, қорытынды үшін бақылау шарттарының тұрақтылығы қажет."),
    "публицистикалық": ("Таза қала", "Қаланың тазалығы әрқайсымызға байланысты. Көшеде қоқыс көбейсе, ортақ кеңістігіміздің қадірі кетеді. Мысалы, ауладағы бір тазалық шарасы көршілерді біріктіре алады. Алайда мәселені тұрғындардың еңбегіне ғана артуға болмайды: қоқыс жәшіктері мен тұрақты тасымал да қажет. Сондықтан тұрғын мен әкімдік бірге әрекет етуі тиіс."),
    "көркем": ("Күз", "Күзгі жел тереземді баяу қақты. Сары жапырақтар балалық шақтың үнсіз хаттарындай аулада айналып жүр. Мен ескі соқпақпен жүріп, әжемнің жылы алақанын еске алдым. Уақыт өзендей алыстаса да, сол мейірім жүректе қалады."),
    "салыстырмалы": ("Оқу түрлері", "Онлайн және дәстүрлі оқу бір мақсатқа қызмет етеді, бірақ мүмкіндіктері бөлек. Онлайн сабақ жол уақытын үнемдейді: шалғайдағы оқушы үйден қатысады. Дәстүрлі сабақта мұғалім оқушының түсінбегенін тезірек байқап, бірден түсіндіре алады. Алайда бұл артықшылық сыныптың көлеміне де тәуелді. Сондықтан таңдау оқушының жағдайы мен пәннің ерекшелігіне сай болуы керек."),
    "өте қысқа": ("Оқу", "Оқу пайдалы."),
}


def main():
    parser = argparse.ArgumentParser()
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--suite", action="store_true", help="Send all 11 cases (uses API quota)")
    selection.add_argument("--case", choices=list(CASES), help="Run one named case")
    args = parser.parse_args()
    settings = Settings()
    if not settings.gemini_api_key.get_secret_value():
        print("BLOCKED: GEMINI_API_KEY is missing. No live AI verification performed.")
        return 2
    with TestClient(create_app(settings)) as client:
        if client.get("/api/health").status_code != 200:
            print("BLOCKED: database is not ready.")
            return 2
        case = args.case or "қалыпты"
        selected = CASES if args.suite else {case: CASES[case]}
        totals = []
        for name, (title, essay) in selected.items():
            response = client.post("/api/essays/analyze", json={"title": title, "essay_text": essay})
            if response.status_code != 201:
                print(f"FAIL {name}: HTTP {response.status_code}; {response.json().get('detail')}")
                return 1
            result = response.json()
            essay_id = result["id"]
            try:
                assert client.get(f"/api/essays/{essay_id}").json() == result
                assert result["total_score"] == sum(c["score"] for c in result["analysis"]["criteria"].values())
                totals.append(result["total_score"])
                print(f"PASS {name}: {result['total_score']}/100; {result['analysis']['classification']['style']}")
            finally:
                assert client.delete(f"/api/essays/{essay_id}").status_code == 204
        if args.suite and len(set(totals)) == 1:
            print("FAIL: all essays received the same total; review grading.")
            return 1
    print("Live integration passed. Academic fairness still requires human review.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
