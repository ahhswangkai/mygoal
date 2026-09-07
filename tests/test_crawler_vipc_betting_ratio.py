import logging
import unittest
from unittest.mock import Mock

from crawler import FootballCrawler
from db_storage import MongoDBStorage
from football_ai.daily_analysis import build_daily_match_input


VIPC_LIST_HTML = r'''
<div class="vSporttery_matchList" data-type="football"
  data-list="[{&quot;scheduleId&quot;:2993777,&quot;home&quot;:&quot;卡利亚里&quot;,&quot;guest&quot;:&quot;莱切&quot;,&quot;matchTime&quot;:&quot;2026-09-08 00:30:00&quot;,&quot;league&quot;:&quot;意甲&quot;,&quot;issue&quot;:&quot;202609071001&quot;,&quot;displayTime&quot;:&quot;周一 001&quot;,&quot;matchId&quot;:&quot;498162138&quot;,&quot;type&quot;:&quot;football&quot;}]">
</div>
'''


VIPC_RATIO_PAYLOAD = {
    'code': 0,
    'data': {
        'jyykRqspf': {
            'a': 1.75,
            'asupportRate': 46,
            'd': 3.3,
            'dsupportRate': 34,
            'goal': '-1.00',
            'h': 3.95,
            'hsupportRate': 20,
        },
        'tzbl': {
            'a': 4,
            'aerror': 1,
            'aprobability': 21,
            'asupportRate': 22,
            'd': 3,
            'derror': 4,
            'dprobability': 30,
            'dsupportRate': 34,
            'h': 1.83,
            'herror': -5,
            'hprobability': 49,
            'hsupportRate': 44,
        },
    },
}


class VipcBettingRatioTest(unittest.TestCase):
    def setUp(self):
        self.crawler = object.__new__(FootballCrawler)
        self.crawler.logger = logging.getLogger(__name__)

    def test_parses_match_identity_from_public_jczq_list(self):
        result = self.crawler.parse_vipc_match_list(VIPC_LIST_HTML)

        self.assertEqual(len(result['matches']), 1)
        match = result['matches'][0]
        self.assertEqual(match['vipc_match_id'], '498162138')
        self.assertEqual(match['vipc_schedule_id'], '2993777')
        self.assertEqual(match['match_number'], '周一001')
        self.assertEqual(match['owner_date'], '2026-09-07')
        self.assertEqual(match['home_team'], '卡利亚里')

    def test_resolves_match_by_owner_date_and_match_number(self):
        listing = self.crawler.parse_vipc_match_list(VIPC_LIST_HTML)
        self.crawler.get_vipc_match_list = lambda: listing

        result = self.crawler.resolve_vipc_match_id({
            'owner_date': '2026-09-07',
            'match_number': '周一001',
            'home_team': '卡利亚里',
            'away_team': '莱切',
        })

        self.assertEqual(result, '498162138')

    def test_rejects_unverified_same_number_from_another_date(self):
        listing = self.crawler.parse_vipc_match_list(VIPC_LIST_HTML)
        self.crawler.get_vipc_match_list = lambda: listing

        result = self.crawler.resolve_vipc_match_id({
            'owner_date': '2026-09-06',
            'match_number': '周一001',
            'home_team': '其他主队',
            'away_team': '其他客队',
            'match_time': '2026-09-07 10:00:00',
        })

        self.assertEqual(result, '')

    def test_parses_ordinary_and_handicap_support_rates(self):
        result = self.crawler.parse_vipc_betting_ratio(VIPC_RATIO_PAYLOAD)

        self.assertEqual(result['source_provider'], 'vipc')
        self.assertEqual(result['ordinary']['home_support_rate'], 44.0)
        self.assertEqual(result['ordinary']['draw_support_rate'], 34.0)
        self.assertEqual(result['ordinary']['away_market_probability'], 21.0)
        self.assertEqual(result['ordinary']['home_error'], -5.0)
        self.assertEqual(result['handicap']['handicap_value'], -1.0)
        self.assertEqual(result['handicap']['home_support_rate'], 20.0)
        self.assertEqual(result['handicap']['draw_support_rate'], 34.0)
        self.assertEqual(result['handicap']['away_support_rate'], 46.0)

    def test_maps_ratio_to_match_and_daily_ai_input(self):
        ratio = self.crawler.parse_vipc_betting_ratio(VIPC_RATIO_PAYLOAD)
        match = {
            'match_id': 'local-001',
            'match_number': '周一001',
            'home_team': '卡利亚里',
            'away_team': '莱切',
        }

        self.crawler._map_odds_details(match, {
            'vipc_match_id': '498162138',
            'betting_ratio': ratio,
        })
        snapshot = build_daily_match_input(match)

        self.assertEqual(match['vipc_match_id'], '498162138')
        self.assertEqual(match['betting_ratio']['ordinary']['draw_support_rate'], 34.0)
        self.assertEqual(snapshot['betting_ratio']['handicap']['away_support_rate'], 46.0)

    def test_persists_vipc_mapping_and_ratio_on_match(self):
        storage = object.__new__(MongoDBStorage)
        storage.logger = logging.getLogger(__name__)
        storage.matches_collection = Mock()
        ratio = self.crawler.parse_vipc_betting_ratio(VIPC_RATIO_PAYLOAD)

        storage._update_match_odds('local-001', {
            'vipc_match_id': '498162138',
            'betting_ratio': ratio,
        })

        args, _ = storage.matches_collection.update_one.call_args
        self.assertEqual(args[0], {'match_id': 'local-001'})
        fields = args[1]['$set']
        self.assertEqual(fields['vipc_match_id'], '498162138')
        self.assertEqual(
            fields['betting_ratio']['ordinary']['home_support_rate'], 44.0
        )


if __name__ == '__main__':
    unittest.main()
