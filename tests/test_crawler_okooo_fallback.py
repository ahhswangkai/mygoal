import logging
import unittest

from crawler import FootballCrawler


OKOOO_LIST_HTML = r'''
<script>var lotterNo = '2026-09-02';</script>
<div class="clearfix center listItem ctrl_eachmatch jsMatchItem" data-mid="328">
  <div class="listname">
    <p class="xuhao">三005</p>
    <a class="liansai">意大利杯</a>
    <time class="timetxt">21:00</time>
    <div matchid="1346795"></div>
  </div>
  <a href="/match/history.php?MatchID=1346795&amp;from=%2Fjczq%2F">
    <em class="ctrl_homename">萨索洛</em>
    <em class="ctrl_awayname">弗洛西</em>
  </a>
</div>
'''


OKOOO_ASIAN_HTML = r'''
<section id="pankou"><table><tbody>
  <tr onclick="window.location='/match/change.php?mid=1346795&amp;pid=35&amp;Type=Handicap'">
    <td><a>易*博</a></td><td><span>1.90</span><em>半球</em><span>1.90</span></td>
    <td><span>1.91</span><em>半球</em><span>1.89</span></td>
  </tr>
  <tr onclick="window.location='/match/change.php?mid=1346795&amp;pid=65&amp;Type=Handicap'">
    <td><a>伟**际</a></td>
    <td class="datetxt01"><span type="zhu">1.84</span><em type="chu">半球</em><span type="ke">1.83</span></td>
    <td class="datetxt01"><span type="xinzhu">1.78</span><em type="xin">半球</em><span type="xinke">1.89</span></td>
  </tr>
</tbody></table></section>
'''


OKOOO_TOTAL_HTML = r'''
<table><tbody>
  <tr class="jsContentItem" index="5">
    <td><a>伟**际</a></td>
    <td><span class="sort-chu-daqiu">1.76</span><span class="filter-chu">2.25</span><span class="sort-chu-xiaoqiu">1.92</span></td>
    <td><span class="sort-xin-daqiu">2.04</span><span class="filter-xin">3.00</span><span class="sort-xin-xiaoqiu">1.67</span></td>
  </tr>
</tbody></table>
'''


class OkoooFallbackTest(unittest.TestCase):
    def setUp(self):
        self.crawler = object.__new__(FootballCrawler)
        self.crawler.logger = logging.getLogger(__name__)

    def test_parses_match_number_and_okooo_id(self):
        result = self.crawler.parse_okooo_match_list(OKOOO_LIST_HTML)

        self.assertEqual(result['owner_date'], '2026-09-02')
        self.assertEqual(len(result['matches']), 1)
        match = result['matches'][0]
        self.assertEqual(match['match_number'], '周三005')
        self.assertEqual(match['okooo_match_id'], '1346795')
        self.assertEqual(match['home_team'], '萨索洛')

    def test_resolves_by_owner_date_and_lottery_number(self):
        listing = self.crawler.parse_okooo_match_list(OKOOO_LIST_HTML)
        self.crawler.get_okooo_match_list = lambda: listing

        result = self.crawler.resolve_okooo_match_id({
            'owner_date': '2026-09-02',
            'match_number': '周三005',
            'home_team': '萨索洛',
            'away_team': '弗洛西',
        })

        self.assertEqual(result, '1346795')

    def test_rejects_same_number_from_another_date(self):
        listing = self.crawler.parse_okooo_match_list(OKOOO_LIST_HTML)
        self.crawler.get_okooo_match_list = lambda: listing

        result = self.crawler.resolve_okooo_match_id({
            'owner_date': '2026-09-01',
            'match_number': '周三005',
        })

        self.assertEqual(result, '')

    def test_parses_weide_asian_and_converts_water(self):
        result = self.crawler.parse_okooo_asian_handicap(OKOOO_ASIAN_HTML)[0]

        self.assertEqual(result['source_company_id'], '65')
        self.assertEqual(result['source_provider'], 'okooo')
        self.assertEqual(result['initial_home_odds'], '0.84')
        self.assertEqual(result['initial_handicap'], '半球')
        self.assertEqual(result['current_home_odds'], '0.78')
        self.assertEqual(result['current_away_odds'], '0.89')

    def test_parses_weide_total_and_preserves_quarter_line(self):
        result = self.crawler.parse_okooo_over_under(OKOOO_TOTAL_HTML)[0]

        self.assertEqual(result['source_company_id'], '65')
        self.assertEqual(result['initial_over_odds'], '0.76')
        self.assertEqual(result['initial_total'], '2.25')
        self.assertEqual(result['current_over_odds'], '1.04')
        self.assertEqual(result['current_total'], '3.00')
        self.assertEqual(result['current_under_odds'], '0.67')

    def test_only_fills_missing_markets(self):
        existing_asian = [{'current_handicap': '半球'}]

        def fake_fetch(url, parser, retries=3):
            if 'ouzhi-' in url:
                return {'euro_odds': []}
            if 'yazhi-' in url:
                return existing_asian
            return None

        self.crawler._fetch_data = fake_fetch
        self.crawler._parse_chinese_handicap = lambda value: 0.5
        calls = []

        def fake_fallback(match, need_asian=True, need_over_under=True):
            calls.append((need_asian, need_over_under))
            return {
                'asian_handicap': [{'current_handicap': '一球'}],
                'over_under': [{'current_total': '2.25'}],
                'okooo_match_id': '1346795',
            }

        self.crawler.crawl_okooo_odds = fake_fallback
        result = self.crawler.crawl_match_odds(
            '500-id',
            match={'match_number': '周三005'},
        )

        self.assertEqual(calls, [(False, True)])
        self.assertIs(result['asian_handicap'], existing_asian)
        self.assertEqual(result['over_under'][0]['current_total'], '2.25')


if __name__ == '__main__':
    unittest.main()
