"""Versioned facts about the BPI Challenge 2019 source dataset."""

from pathlib import Path

BPI_2019_DATASET_NAME = "BPI Challenge 2019"
BPI_2019_DOI = "10.4121/uuid:d06aff4b-79f0-45e6-8ec8-e19730c248f1"
BPI_2019_ARTICLE_URL = "https://data.4tu.nl/articles/dataset/BPI_Challenge_2019/12715853"
BPI_2019_DOWNLOAD_URL = "https://ndownloader.figshare.com/files/24072995"
BPI_2019_FILENAME = "BPI_Challenge_2019.xes"
BPI_2019_MD5 = "4eb909242351193a61e1c15b9c3cc814"
BPI_2019_SIZE_BYTES = 728_558_522
BPI_2019_EXPECTED_CASES = 251_734
BPI_2019_EXPECTED_EVENTS = 1_595_923
BPI_2019_EXPECTED_ACTIVITIES = 42
DEFAULT_XES_PATH = Path("/app/data/raw") / BPI_2019_FILENAME
