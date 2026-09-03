import logging
import unittest

from crawler import FootballCrawler


SPORTTERY_PAYLOAD = {
    'success': True,
    'value': {
        'matchInfoList': [{
            'businessDate': '2026-09-03',
            'subMatchList': [{
                'matchId': 2041234,
                'matchNumStr': '周四001',
                'matchTime': '23:55:00',
                'homeTeamAbbName': '迈季宽广',
                'awayTeamAbbName': '拉斯永恒',
                'leagueAbbName': '沙职',
                'had': {
                    'h': '2.65', 'd': '3.05', 'a': '2.36',
                    'hf': '1', 'df': '0', 'af': '-1',
                    'updateDate': '2026-09-02',
                    'updateTime': '09:26:06',
                },
                'hhad': {
                    'h': '1.45', 'd': '3.95', 'a': '5.35',
                    'hf': '0', 'df': '1', 'af': '0',
                    'goalLine': '+1', 'goalLineValue': '+1.00',
                    'updateDate': '2026-09-02',
                    'updateTime': '09:26:06',
                },
            }],
        }],
    },
}


class SportteryOddsTest(unittest.TestCase):
    def setUp(self):
        self.crawler = object.__new__(FootballCrawler)
        self.crawler.logger = logging.getLogger(__name__)

    def test_parses_and_resolves_calculator_match(self):
        listing = self.crawler.parse_sporttery_calculator(SPORTTERY_PAYLOAD)
        self.crawler.get_sporttery_calculator_list = lambda: listing

        result = self.crawler.resolve_sporttery_match({
            'owner_date': '2026-09-03',
            'match_number': '周四001',
        })

        self.assertEqual(result['sporttery_match_id'], '2041234')
        self.assertEqual(result['home_team'], '迈季宽广')
        self.assertEqual(result['match_time'], '23:55')

    def test_builds_compatible_odds_and_preserves_initial_baseline(self):
        listing = self.crawler.parse_sporttery_calculator(SPORTTERY_PAYLOAD)
        self.crawler.get_sporttery_calculator_list = lambda: listing

        result = self.crawler.crawl_sporttery_odds({
            'owner_date': '2026-09-03',
            'match_number': '周四001',
            'euro_initial_win': '2.80',
            'euro_initial_draw': '3.20',
            'euro_initial_lose': '2.20',
            'hi_initial_home_odds': '1.50',
            'hi_initial_draw_odds': '4.00',
            'hi_initial_away_odds': '5.10',
        })

        euro = result['euro_odds'][0]
        handicap = result['handicap_index']
        self.assertEqual(euro['current_win'], '2.65')
        self.assertEqual(euro['initial_win'], '2.80')
        self.assertEqual(euro['win_flag'], 1)
        self.assertEqual(euro['source_provider'], 'sporttery-calculator')
        self.assertEqual(handicap['handicap_value'], '+1.00')
        self.assertEqual(handicap['initial_home_odds'], '1.50')
        self.assertEqual(handicap['draw_flag'], 1)
        self.assertEqual(result['sporttery_match_id'], '2041234')


if __name__ == '__main__':
    unittest.main()
