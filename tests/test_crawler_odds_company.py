import logging
import unittest

from crawler import FootballCrawler


def company_row(company_id, name, current, initial, row_class="tr1"):
    values = [
        "序号",
        f'<a title="{name}">{name}</a>',
        "占位",
        current[0],
        current[1],
        current[2],
        "时间",
        "占位",
        "占位",
        initial[0],
        initial[1],
        initial[2],
    ]
    cells = []
    for index, value in enumerate(values):
        css_class = ' class="tb_plgs"' if index == 1 else ""
        cells.append(f"<td{css_class}>{value}</td>")
    return (
        f'<tr class="{row_class}" xls="row" id="{company_id}">'
        + "".join(cells)
        + "</tr>"
    )


def odds_table(*rows):
    return '<table id="datatb">' + "".join(rows) + "</table>"


class PreferredOddsCompanyTest(unittest.TestCase):
    def setUp(self):
        self.crawler = object.__new__(FootballCrawler)
        self.crawler.logger = logging.getLogger(__name__)

    def test_asian_prefers_company_six(self):
        html = odds_table(
            company_row(
                "1484",
                "**t3*5",
                ("0.85", "一球", "0.93"),
                ("0.83", "一球", "0.95"),
            ),
            company_row(
                "6",
                "伟*",
                ("0.87↓", "一球降", "0.92↑"),
                ("1.04", "一球/球半", "0.77"),
                row_class="tr2",
            ),
        )

        result = self.crawler.parse_asian_handicap(html)[0]

        self.assertEqual(result["source_company_id"], "6")
        self.assertEqual(result["source_company_name"], "伟*")
        self.assertFalse(result["source_fallback"])
        self.assertEqual(result["current_home_odds"], "0.87")
        self.assertEqual(result["current_handicap"], "一球")
        self.assertEqual(result["initial_handicap"], "一球/球半")

    def test_over_under_prefers_company_six(self):
        html = odds_table(
            company_row(
                "1484",
                "竞*官*(中国)",
                ("1.30", "3.5", "0.57"),
                ("1.30", "3.5", "0.57"),
            ),
            company_row(
                "6",
                "伟*",
                ("0.85↑", "3降", "0.92↓"),
                ("0.84", "3", "0.94"),
                row_class="tr2",
            ),
        )

        result = self.crawler.parse_over_under(html)[0]

        self.assertEqual(result["source_company_id"], "6")
        self.assertEqual(result["source_company_name"], "伟*")
        self.assertFalse(result["source_fallback"])
        self.assertEqual(result["current_over_odds"], "0.85")
        self.assertEqual(result["current_total"], "3")
        self.assertEqual(result["initial_total"], "3")

    def test_marks_fallback_when_company_six_is_missing(self):
        html = odds_table(
            company_row(
                "1484",
                "**t3*5",
                ("0.85", "一球", "0.93"),
                ("0.83", "一球", "0.95"),
            )
        )

        result = self.crawler.parse_asian_handicap(html)[0]

        self.assertEqual(result["source_company_id"], "1484")
        self.assertTrue(result["source_fallback"])


if __name__ == "__main__":
    unittest.main()
