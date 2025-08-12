import random
import re
import string
from collections import defaultdict
from dataclasses import dataclass, field
from typing import List, Dict, Union
import datetime

import requests
from bs4 import BeautifulSoup
from lxml import etree
from requests_toolbelt import MultipartEncoder

MAGISTREJTIK_NUMBER = re.compile(r"Магистрейтик ([0-9]+) сезон")
NUMBER = re.compile(r"[0-9]+")
MAIN_URL = "https://mafiauniverse.org"
VALID_SERIES = re.compile(r"серия [0-9]+ | стол [0-9]")
NICKNAME_AND_MVP = re.compile(r"(\w+ ?\w*)[\n\r\s]+[0-9,\-]+[\n\r\s]+([0-9,\-]+)")

NICKNAMES = {}

@dataclass
class Series:
    places: List[str]
    points: List[int]
    dops_sum: float = field(init=False)
    
    def __post_init__(self):
        self.dops_sum = sum(i for i in self.points if i > 0)


def parse_season(html_content: bytes):
    soup = BeautifulSoup(html_content, "html.parser")
    max_season_number = 0
    html_link = ""
    for a_link in soup.find_all("a"):
        season_number = MAGISTREJTIK_NUMBER.findall(a_link.text)
        if season_number:
            season_num = int(season_number[0])
            if season_num > max_season_number:
                max_season_number = season_num
                html_link = a_link["href"]
    series_number = NUMBER.findall(html_link)
    return series_number[0] if series_number else None


def get_series(session: requests.Session):
    
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
        "searchText": "Магистрейтик",
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


def parse_tournament_links(html_content: bytes) -> List[str]:
    soup = BeautifulSoup(html_content, "html.parser")
    links = [
        item["href"]
        for item in soup.find_all("a", class_="fw-bold")
        if VALID_SERIES.search(item.text)
    ]
    links.reverse()
    return links


def parse_series(result_table, exclude: List[str]) -> Series:
    places = []
    points = []
    for tr in result_table.find_all("tr"):
        tds = tr.find_all("td")
        if not tds:
            continue
        nickname_real = re.findall(r"\w+ ?\w*", tds[1].text)[0].rstrip(" ")
        nickname = nickname_real.lower()
        NICKNAMES[nickname] = nickname_real
        if nickname in exclude:
            continue
        else:
            places.append(nickname)
            additional_ball = float(re.findall(r"[0-9,\-]+", tds[3].text)[0].replace(",", "."))
            points.append(additional_ball)

    return Series(places, points)


def calculate_rating(series: List[Series], exclude: list[str]):
    rating = {}
    series_count = {}
    for item in series:
        total = 25
        for player in item.places:
            rating[player] = rating.get(player, 100)
            total += rating[player] * 0.1
            rating[player] -= rating[player] * 0.1
            series_count[player] = series_count.get(player, 0) + 1
        if item.dops_sum <= 0:
            tops_distribution = 1
        else:
            tops_distribution = 0.5
        dops_distribution = 1 - tops_distribution
        rating[item.places[0]] += tops_distribution * total * 0.50
        rating[item.places[1]] += tops_distribution * total * 0.33
        rating[item.places[2]] += tops_distribution * total * 0.17
        
        for i, player in enumerate(item.places):
            if item.points[i] > 0 :
                rating[player] += item.points[i]/item.dops_sum * dops_distribution * total
        # with open('file.txt', 'a', encoding='utf-8') as f:
        #     print(sum(rating.values()), file=f)
        #     for player in rating:
        #         print(player, rating[player], file=f)
        #     print('\n', file=f)
    data = {
        name: {"points": rating[name], "series count": series_count[name]}
        for name in rating
        if name not in exclude
    }
    return data


def get_real_rating(exclude: list[str]) -> Dict[str, Dict[str, Union[int, float]]]:
    exclude = [item.lower() for item in exclude]
    session = requests.Session()
    response = get_series(session)
    series_number = parse_season(response.content)
    if series_number is None:
        raise RuntimeError("Season number wasn't found")
    series_url = f"{MAIN_URL}/SerieOfTournament/Tournaments/{series_number}"
    response = session.get(series_url)
    links = parse_tournament_links(response.content)
    if not links:
        raise RuntimeError("Tournaments weren't found")
    series = []
    for link in links:
        tournament_link = f"{MAIN_URL}{link}"
        response = session.get(tournament_link)
        soup = BeautifulSoup(response.content, "html.parser")
        result_table = soup.find("table", {"id": "TournResultsTable"})
        if not result_table.find('tbody').text == '\n':
            series.append(parse_series(result_table, exclude))
    rating = calculate_rating(series, exclude)
    rating = dict(
        sorted(rating.items(), key=lambda item: item[1]["points"], reverse=True)
    )
    return rating


def math_round(number: float) -> int:
    number = round(number, 1)
    main_part = int(number)
    if (number - main_part) * 10 >= 5:
        main_part += 1
    return main_part


def get_rating(exclude: list[str]) -> list[dict[str, str]]:
    real_rating = get_real_rating(exclude)
    rating = [
        {
            'nickname': NICKNAMES[nick],
            'rating': str(math_round(data['points'])),
            'series_count': str(data['series count'])
        }
        for nick, data in real_rating.items()
    ]
    return rating


def get_player_rating(player: str, exclude: List[str]) -> dict[str, str]:
    rating = get_rating(exclude)
    for place, player_data in enumerate(rating, start=1):
        if player_data['nickname'] == player:
            data = player_data.copy()
            data['place'] = place
            return data
