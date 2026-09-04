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
  <tr onclick="window.location='/match/change.php?mid=1346795&amp;pid=27&amp;Type=Handicap'">
    <td><a>B**365</a></td>
    <td class="datetxt01"><span type="zhu">1.93</span><em type="chu">半/一</em><span type="ke">1.93</span></td>
    <td class="datetxt01"><span type="xinzhu">1.83</span><em type="xin">半/一</em><span type="xinke">2.03</span></td>
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
  <tr class="jsContentItem" index="6" onclick="window.location='/match/change.php?mid=1346795&amp;pid=27&amp;Type=OverUnder'">
    <td><a>B**365</a></td>
    <td><span class="sort-chu-daqiu">1.90</span><span class="filter-chu">2.50</span><span class="sort-chu-xiaoqiu">1.90</span></td>
    <td><span class="sort-xin-daqiu">1.86</span><span class="filter-xin">2.50</span><span class="sort-xin-xiaoqiu">1.94</span></td>
  </tr>
</tbody></table>
'''


OKOOO_ASIAN_FIRST_COMPANY_HTML = r'''
<section id="pankou"><table><tbody>
  <tr onclick="window.location='/match/change.php?mid=1346795&amp;pid=35&amp;Type=Handicap'">
    <td><a>易*博</a></td>
    <td><span>1.91</span><em>平/半</em><span>1.89</span></td>
    <td><span>1.86</span><em>半球</em><span>1.94</span></td>
  </tr>
</tbody></table></section>
'''


OKOOO_TOTAL_FIRST_COMPANY_HTML = r'''
<table><tbody>
  <tr class="jsContentItem" index="1">
    <td><a>澳*门</a></td>
    <td><span class="sort-chu-daqiu">1.85</span><span class="filter-chu">2.25</span><span class="sort-chu-xiaoqiu">1.85</span></td>
    <td><span class="sort-xin-daqiu">1.90</span><span class="filter-xin">2.50</span><span class="sort-xin-xiaoqiu">1.80</span></td>
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

    def test_resolves_cross_date_listing_when_teams_match(self):
        listing = self.crawler.parse_okooo_match_list(OKOOO_LIST_HTML)
        self.crawler.get_okooo_match_list = lambda: listing

        result = self.crawler.resolve_okooo_match_id({
            'owner_date': '2026-09-03',
            'match_number': '周三005',
            'home_team': '萨索洛',
            'away_team': '弗洛西',
        })

        self.assertEqual(result, '1346795')

    def test_resolves_cross_date_listing_when_owner_weekday_matches(self):
        listing = self.crawler.parse_okooo_match_list(OKOOO_LIST_HTML)
        self.crawler.get_okooo_match_list = lambda: listing

        result = self.crawler.resolve_okooo_match_id({
            'owner_date': '2026-09-02',
            'match_number': '周三005',
            'home_team': '不同译名A',
            'away_team': '不同译名B',
        })

        self.assertEqual(result, '1346795')

    def test_parses_b365_asian_and_converts_water(self):
        result = self.crawler.parse_okooo_asian_handicap(OKOOO_ASIAN_HTML)[0]

        self.assertEqual(result['source_company_id'], '27')
        self.assertEqual(result['source_company_name'], 'B**365')
        self.assertEqual(result['source_provider'], 'okooo')
        self.assertEqual(result['initial_home_odds'], '0.93')
        self.assertEqual(result['initial_handicap'], '半/一')
        self.assertEqual(result['current_home_odds'], '0.83')
        self.assertEqual(result['current_away_odds'], '1.03')

    def test_parses_b365_total_and_preserves_quarter_line(self):
        result = self.crawler.parse_okooo_over_under(OKOOO_TOTAL_HTML)[0]

        self.assertEqual(result['source_company_id'], '27')
        self.assertEqual(result['source_company_name'], 'B**365')
        self.assertEqual(result['initial_over_odds'], '0.90')
        self.assertEqual(result['initial_total'], '2.50')
        self.assertEqual(result['current_over_odds'], '0.86')
        self.assertEqual(result['current_total'], '2.50')
        self.assertEqual(result['current_under_odds'], '0.94')

    def test_asian_falls_back_to_first_valid_company(self):
        result = self.crawler.parse_okooo_asian_handicap(
            OKOOO_ASIAN_FIRST_COMPANY_HTML
        )[0]

        self.assertEqual(result['source_company_id'], '')
        self.assertEqual(result['source_company_name'], '易*博')
        self.assertEqual(result['initial_handicap'], '平/半')
        self.assertEqual(result['current_handicap'], '半球')

    def test_total_falls_back_to_first_valid_company(self):
        result = self.crawler.parse_okooo_over_under(
            OKOOO_TOTAL_FIRST_COMPANY_HTML
        )[0]

        self.assertEqual(result['source_company_id'], '')
        self.assertEqual(result['source_company_name'], '澳*门')
        self.assertEqual(result['initial_total'], '2.25')
        self.assertEqual(result['current_total'], '2.50')

    def test_uses_sporttery_and_okooo_without_500_odds_pages(self):
        self.crawler._fetch_data = lambda *args, **kwargs: self.fail(
            '不应再请求500赔率页面'
        )
        calls = []

        def fake_sporttery(match):
            return {
                'euro_odds': [{'current_win': '2.10'}],
                'handicap_index': {'handicap_value': '-1'},
                'sporttery_match_id': '2041234',
            }

        def fake_okooo(match, need_asian=True, need_over_under=True):
            calls.append((need_asian, need_over_under))
            return {
                'asian_handicap': [{'current_handicap': '一球'}],
                'over_under': [{'current_total': '2.25'}],
                'okooo_match_id': '1346795',
            }

        self.crawler.crawl_sporttery_odds = fake_sporttery
        self.crawler.crawl_okooo_odds = fake_okooo
        result = self.crawler.crawl_match_odds(
            '500-id',
            match={'match_number': '周三005'},
        )

        self.assertEqual(calls, [(True, True)])
        self.assertEqual(result['euro_odds'][0]['current_win'], '2.10')
        self.assertEqual(result['handicap_index']['handicap_value'], '-1')
        self.assertEqual(result['asian_handicap'][0]['current_handicap'], '一球')
        self.assertEqual(result['over_under'][0]['current_total'], '2.25')
        self.assertEqual(result['sporttery_match_id'], '2041234')
        self.assertEqual(result['okooo_match_id'], '1346795')


if __name__ == '__main__':
    unittest.main()
