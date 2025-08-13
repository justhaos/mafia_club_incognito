import requests
from bs4 import BeautifulSoup
from lxml import etree
import pandas as pd
from math import ceil
from datetime import datetime

from requests_toolbelt import MultipartEncoder
import random
import re
import string

MAIN_URL = "https://mafiauniverse.org"

def get_series(session: requests.Session):
    '''функция делает HTTP-запрос к веб-серверу для получения данных о сыгранных сериях в сезоне. '''
    token_request = session.get(F"{MAIN_URL}/SerieOfTournaments/")
    parser = etree.HTMLParser()
    tree = etree.fromstring(token_request.text, parser)
    verificationToken = tree.xpath(
        '//form//input[@name="__RequestVerificationToken"]/@value'
    )[0]
    session_cookies = token_request.cookies
    fields = {
        "Page": "1",
        "Year": str(datetime.now().year),
        "searchText": "Энтропия",
        "PageSize": "25",
    }
    boundary = "----WebKitFormBoundary" + "".join(
        random.sample(string.ascii_letters + string.digits, 16)
    )
    m = MultipartEncoder(fields=fields, boundary=boundary)
    headers = {
        "requestverificationtoken": str(verificationToken),
        "Content-Type": m.content_type,
    }
    return session.post(
        f"{MAIN_URL}/SerieOfTournaments/Search",
        cookies=session_cookies,
        headers=headers,
        data=m,
    )

def parse_season(html_content: bytes):
    '''Функция находит ссылочный номер (не порядковый) сезона.'''
    soup = BeautifulSoup(html_content, "html.parser")
    for a_link in soup.find_all("a"):
        if re.compile(r"энтропия", re.IGNORECASE).findall(a_link.text):
            sot_num = re.compile("[0-9]+").findall(a_link["href"])
    return int(sot_num[0])

class Season():
    '''
    Parameters
    ----------
    link_season_num : int
        Номер сезона магистрейтика в ссылке на МЮ
    exclude : list[str]
        Список игроков вне зачета
    '''

    def __init__(self, session, link_season_num):

        self._link_season_num = link_season_num
        response = session.get(f"{MAIN_URL}/SerieOfTournament/Tournaments/{link_season_num}")
        self.links = self.parse_tournament_links(response.content)

        self.place_fine = {
            1: 20,
            2: 15,
            3: 10,
            4: 0,
            5: -10,
            6: -15,
            7: -20,
            8: -25,
            9: -30,
            10: -35
        }

        self.RATING = {}
        self.TOURS_COUNT = {}
        self.DOPS = {}
        self.POINTS = {}

        self.calculate_rating_over_season()
    
    @staticmethod
    def parse_tournament_links(html_content: bytes):
        '''Функция находит ссылки на сыгранные серии'''
        soup = BeautifulSoup(html_content, "html.parser")
        links = [
            item["href"]
            for item in soup.find_all("a", class_="fw-bold")
            if not re.compile(r"вне зачета", re.IGNORECASE).search(item.text)
        ]
        links.reverse()
        if not links:
            raise RuntimeError("Tournaments weren't found")
        return links
    
    def create_pd_of_tour_results(self, link, session):
        ''' Функция создат датафрейм результатов серии (без внезачетных игроков) '''
        tournament_link = f"{MAIN_URL}{link}"
        soup = BeautifulSoup(session.get(tournament_link).content, "html.parser")
        serya_num = self.links.index(link) + 1
        result_table = soup.find("table", {"id": "TournResultsTable"})
        if not result_table:
            print(serya_num, tournament_link)
            raise RuntimeError
        results = []
        for row in result_table.find('tbody').find_all('tr'):
            elements = list(map(lambda x: x.text.strip(), row.find_all('td')))
            place = int(elements[0])
            nick = elements[1]
            points = float(elements[2].replace(',', '.'))
            dops = float(elements[3].replace(',', '.'))
            wins = int(re.compile("[0-9]+").findall(elements[9])[0])
            games = int(re.compile("[0-9]+").findall(elements[9])[1])
            results.append((serya_num, nick, place, points, dops, wins, games))

            self.RATING[nick] = self.RATING.get(nick, 100)
            self.TOURS_COUNT[nick] = self.TOURS_COUNT.get(nick, 0) + 1
            self.DOPS[nick] = self.DOPS.get(nick, 0) + dops
            self.POINTS[nick] = self.POINTS.get(nick, 0) + points

        tour_results = pd.DataFrame(results, columns=['Tour', 'Nick', 'Place', 'Points', 'Dops', 'Wins', 'Games'])\
                         .sort_values(by='Points', ascending=False)
        return tour_results
    
    def rating_formula(self, tour_results:pd.DataFrame):
        results = tour_results.copy()
        results['Rating_before'] = [self.RATING[results.loc[i, 'Nick']] for i in results.index]
        results['place_fine'] = [self.place_fine[i] for i in results.Place]
        results['points_effect'] = (results.Dops * 20 + results.Wins * 10)* 5/results.Games
        results['Delta'] = results['place_fine'] + results['points_effect']
        results['Rating'] = results['Rating_before'] + results['Delta']
        return results
    
    def calculate_rating_over_season(self):
        history = []
        for link in self.links:
            tour_results = self.create_pd_of_tour_results(link, session = requests.Session())
            rating_after_tour = self.rating_formula(tour_results)
            history.append(rating_after_tour)
            for i, r in rating_after_tour.iterrows():
                self.RATING[r.Nick] = r.Rating
        self.history = pd.concat(history, ignore_index=True)

    @property
    def finalysts(self):
        result = []
        for x in sorted(self.RATING, key=lambda x: -self.RATING[x]):
            if self.TOURS_COUNT[x] >= 3:
                result.append({
                                "nickname": x,
                                "rating": self.RATING[x],
                                "series_count": self.TOURS_COUNT[x],
                                'total_dops' : self.DOPS[x],
                                'total_points' : self.POINTS[x],
                                'avg_points' : self.POINTS[x] / self.TOURS_COUNT[x]
                                })
        # result = result[:10]
        result = pd.DataFrame(result).sort_values('rating', ascending=False, ignore_index=True)
        return result
    
    @property
    def current_rating(self):
        result = []
        for x in sorted(self.RATING, key=lambda x: -self.RATING[x]):
            result.append({
                            "nickname": x,
                            "rating": ceil(self.RATING[x]),
                            "series_count": self.TOURS_COUNT[x]
                            })
        return result
    
    def _repr_html_(self):
        result = []
        for x in self.RATING:
            result.append({
                            "nickname": x,
                            "rating": self.RATING[x],
                            "series_count": self.TOURS_COUNT[x],
                            'total_dops' : self.DOPS[x],
                            'total_points' : self.POINTS[x],
                            'avg_points' : self.POINTS[x] / self.TOURS_COUNT[x]
                            })
        result = pd.DataFrame(result).sort_values('rating', ascending=False, ignore_index=True)
        return result.head(20)._repr_html_()
    

def get_rating():
    session = requests.Session()
    response = get_series(session)
    series_number = parse_season(response.content)
    s = Season(session, series_number)
    return s.current_rating


def get_player_rating(player: str) -> dict[str, str]:
    rating = get_rating()
    for place, player_data in enumerate(rating, start=1):
        if player_data['nickname'] == player:
            data = player_data.copy()
            data['place'] = place
            return data