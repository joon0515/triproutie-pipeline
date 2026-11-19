"""257기 transit_packs — 네트워크·키 없이(가짜 fetch·만든 GTFS)."""
import io
import json
import os
import sys
import unittest
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
import transit_packs as tp  # noqa: E402

MTR = ('"SRC_STATION_NAME","SRC_STATION_ID","DEST_STATION_NAME","DEST_STATION_ID","OCT_ADT_FARE","OCT_STD_FARE","OCT_JOYYOU_SIXTY_FARE","SINGLE_ADT_FARE","OCT_CON_CHILD_FARE","OCT_CON_ELDERLY_FARE","OCT_CON_PWD_FARE","SINGLE_CON_CHILD_FARE","SINGLE_CON_ELDERLY_FARE"\n'
       '"Central",1,"Central",1,0.00,0.00,0.00,0.00,0.00,0.00,0.00,0.00,0.00\n'
       '"Central",1,"Admiralty",2,4.90,3.20,2.00,5.00,3.20,2.00,2.00,3.50,3.50\n'
       '"Admiralty",2,"Central",1,4.90,3.20,2.00,5.00,3.20,2.00,2.00,3.50,3.50\n').encode()
SG = {'result': {'records': [
    {'fare_type': 'Adult card fare', 'applicable_time': 'All other timings', 'distance': 'Up to 3.2 km', 'fare_per_ride': '128'},
    {'fare_type': 'Adult card fare', 'applicable_time': 'All other timings', 'distance': '3.3 km - 4.2 km', 'fare_per_ride': '138'},
    {'fare_type': 'Adult card fare', 'applicable_time': 'All other timings', 'distance': 'Over 40.2 km', 'fare_per_ride': '257'},
    {'fare_type': 'Adult card fare', 'applicable_time': 'Before 7.45am  (Weekdays excluding public holidays)', 'distance': 'Up to 3.2 km', 'fare_per_ride': '78'},
    {'fare_type': 'Student card fare', 'applicable_time': 'All other timings', 'distance': 'Up to 3.2 km', 'fare_per_ride': '60'}]}}
ST = [{'owl:sameAs': 'odpt.Station:Toei.Oedo.Tochomae', 'dc:title': '都庁前', 'odpt:stationTitle': {'ja': '都庁前', 'en': 'Tochomae'}, 'geo:lat': 35.690, 'geo:long': 139.692},
      {'owl:sameAs': 'odpt.Station:Toei.Oedo.Roppongi', 'dc:title': '六本木', 'odpt:stationTitle': {'ja': '六本木', 'en': 'Roppongi'}, 'geo:lat': 35.663, 'geo:long': 139.731},
      {'owl:sameAs': 'odpt.Station:Toei.Oedo.Nolatlon'}]
FARE = [{'odpt:fromStation': 'odpt.Station:Toei.Oedo.Tochomae', 'odpt:toStation': 'odpt.Station:Toei.Oedo.Roppongi', 'odpt:icCardFare': 220, 'odpt:ticketFare': 220}]
STT = [{'odpt:station': 'odpt.Station:Toei.Oedo.Tochomae', 'odpt:railway': 'odpt.Railway:Toei.Oedo', 'odpt:calendar': 'odpt.Calendar:Weekday',
        'odpt:stationTimetableObject': [{'odpt:departureTime': '05:07', 'odpt:destinationStation': ['odpt.Station:Toei.Oedo.Roppongi']},
                                        {'odpt:departureTime': '00:15', 'odpt:destinationStation': ['odpt.Station:Toei.Oedo.Roppongi']}]},
       {'odpt:station': 'odpt.Station:Toei.Oedo.Tochomae', 'odpt:calendar': 'odpt.Calendar:Unknown', 'odpt:stationTimetableObject': []}]


def gtfs_zip():
    b = io.BytesIO()
    with zipfile.ZipFile(b, 'w') as z:
        z.writestr('calendar.txt', 'service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date\nWK,1,1,1,1,1,0,0,20260101,20271231\nSU,0,0,0,0,0,0,1,20260101,20271231\n')
        z.writestr('routes.txt', 'route_id,route_short_name,route_long_name,route_type\nR1,都01,,3\n')
        z.writestr('trips.txt', 'route_id,service_id,trip_id,trip_headsign\nR1,WK,T1,新橋駅前\nR1,WK,T2,新橋駅前\nR1,SU,T3,渋谷駅前\nR1,XX,T4,없는 운행\n')
        z.writestr('stops.txt', 'stop_id,stop_name,stop_lat,stop_lon\nS1,渋谷駅前,35.6590,139.7016\nS2,六本木,35.6633,139.7310\n')
        z.writestr('stop_times.txt', 'trip_id,arrival_time,departure_time,stop_id,stop_sequence,pickup_type\nT1,08:00:00,08:00:00,S1,1,0\nT1,08:10:00,08:10:00,S2,2,1\nT2,08:20:00,08:20:00,S1,1,0\nT3,25:05:00,25:05:00,S1,1,0\nT4,09:00:00,09:00:00,S1,1,0\n')
    return b.getvalue()


class T(unittest.TestCase):
    def test_mtr(self):
        r = tp.mtr_pairs(MTR)
        self.assertEqual(r['fares']['1']['2'], 4.9)
        self.assertNotIn('1', r['fares'].get('1', {}))
        self.assertEqual(r['stations'][0], ['1', 'Central'])

    def test_sg(self):
        self.assertEqual(tp.sg_bands(SG), [[3.2, 128], [4.2, 138], [999.0, 257]])

    def test_odpt(self):
        st = tp.odpt_stations(ST)
        self.assertEqual(len(st), 2)
        self.assertEqual(tp.odpt_fares(FARE)['odpt.Station:Toei.Oedo.Tochomae']['odpt.Station:Toei.Oedo.Roppongi'], 220)
        d = tp.odpt_station_timetables(STT, st)
        row = d['odpt.Station:Toei.Oedo.Tochomae'][0]
        self.assertEqual(row[:3], ['Toei.Oedo', 'Roppongi', 0b0011111])
        self.assertEqual(row[3], [307, 1455])  # 00:15 = 전날 밤 24:15

    def test_gtfs(self):
        stops, deps = tp.gtfs_departures(gtfs_zip(), 'mdb-3175')
        self.assertEqual([s[0] for s in stops], ['mdb-3175:S1'])  # S2 는 내리기만(pickup_type=1)
        d = deps['mdb-3175:S1']
        self.assertIn(['都01', '新橋駅前', 0b0011111, [480, 500]], d)
        self.assertIn(['都01', '渋谷駅前', 0b1000000, [1505]], d)
        self.assertFalse(any(x[1] == '없는 운행' for x in d))

    def test_fares_doc(self):
        def fake(url):
            if 'mtr' in url:
                return MTR
            if 'data.gov.sg' in url:
                return json.dumps(SG).encode()
            if 'odpt:Station' in url:
                return json.dumps(ST).encode()
            if 'RailwayFare' in url:
                return json.dumps(FARE).encode()
            raise RuntimeError(url)
        d = tp.build_fares(fetch=fake)
        c = d['cities']
        for k in ('seoul', 'nyc', 'seattle', 'paris', 'london', 'busan', 'hongkong', 'singapore', 'tokyo_toei'):
            self.assertIn(k, c)
            self.assertTrue(c[k]['src'] and all(s['url'].startswith('https://') and s['checked'] for s in c[k]['src']), k)
        self.assertEqual(c['seoul']['rule']['subway'], 1550)
        self.assertEqual(c['paris']['rule']['rail'], 2.55)
        self.assertEqual(c['nyc']['cap'], {'rides': 12, 'days': 7, 'price': 35.0})
        self.assertEqual(c['busan']['rule'], {'type': 'link_only'})
        self.assertNotIn('추정', json.dumps(d, ensure_ascii=False))

    def test_fares_partial_fail(self):
        def bad(url):
            raise RuntimeError('net')
        c = tp.build_fares(fetch=bad)['cities']
        self.assertNotIn('hongkong', c)
        self.assertIn('seoul', c)

    def test_timetable_doc(self):
        p = os.path.join(os.path.dirname(__file__), '_tt_tmp.zip')
        with open(p, 'wb') as f:
            f.write(gtfs_zip())
        try:
            d = tp.build_timetable('tokyo', [(p, {'id': 'mdb-3175', 'license': 'CC BY 4.0', 'attr': '© 東京都交通局'})])
        finally:
            os.remove(p)
        self.assertEqual(d['basis'], 'weekday_calendar')
        self.assertEqual(d['src'][0]['license'], 'CC BY 4.0')
        self.assertIn('mdb-3175:S1', d['deps'])


if __name__ == '__main__':
    unittest.main()
