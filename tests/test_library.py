import pytest
from pydantic import ValidationError

from music_curator.demo import PROTOTYPE_TRACKS
from music_curator.library import SongFilters, song_collection
from music_curator.models import Album, Artist, SavedTrack


@pytest.mark.parametrize(
    ("release_date", "precision", "label", "year"),
    [
        ("2024-02-29", "day", "29/02/2024", "2024"),
        ("2024-04", "month", "04/2024", "2024"),
        ("2022", "year", "2022", "2022"),
        (None, None, "Não informada", None),
    ],
)
def test_release_dates_preserve_available_precision(
    release_date: str | None, precision: str | None, label: str, year: str | None
) -> None:
    album = Album.model_validate(
        {
            "name": "Album sintetico",
            "release_date": release_date,
            "release_date_precision": precision,
        }
    )
    assert album.release_label == label
    assert album.release_year == year
    assert "01/01/" not in album.release_label


@pytest.mark.parametrize(
    "release_date",
    ["", "not-a-date", "2023-02-29", "2024-13", "2024-00-01", "0000", "2024-4", "2024-04-31"],
)
def test_invalid_release_dates_are_rejected(release_date: str) -> None:
    with pytest.raises(ValidationError):
        Album(name="Album sintetico", release_date=release_date)


def test_release_precision_must_match_date_shape() -> None:
    with pytest.raises(ValidationError):
        Album(name="Album sintetico", release_date="2024", release_date_precision="day")
    assert Album(name="Album sintetico", release_date="2024-04").release_label == "04/2024"


@pytest.mark.parametrize(
    ("fields", "indices"),
    [
        ({}, [0, 1, 2, 3, 4, 5]),
        ({"q": "   cAfE   "}, [1]),
        ({"q": "CLARA pausas"}, [1, 5]),
        ({"q": "aurora novos"}, [0, 3]),
        ({"artist": "Banda Aurora"}, [0, 3]),
        ({"album": "Pequenas pausas"}, [1, 5]),
        ({"year": "2024"}, [1, 5]),
        ({"year": "2022"}, [2]),
        (
            {"q": "tranquilo", "artist": "Banda Aurora", "album": "Novos caminhos", "year": "2023"},
            [3],
        ),
        ({"q": "nao existe"}, []),
        ({"artist": "Banda Aurora", "year": "2024"}, []),
    ],
)
def test_filters_combine_without_modifying_imported_collection(
    fields: dict[str, str], indices: list[int]
) -> None:
    before = [item.model_dump() for item in PROTOTYPE_TRACKS]
    result = song_collection(PROTOTYPE_TRACKS, SongFilters.model_validate(fields))
    assert result.tracks == [PROTOTYPE_TRACKS[index] for index in indices]
    assert [item.model_dump() for item in PROTOTYPE_TRACKS] == before
    assert result.artists == ["Banda Aurora", "Clara do Sol", "Coletivo Vento", "Trio Horizonte"]
    assert result.albums == [
        "Depois da chuva",
        "Entre chegadas",
        "Novos caminhos",
        "Pequenas pausas",
    ]
    assert result.years == ["2025", "2024", "2023", "2022"]


def test_artist_filter_includes_collaborations() -> None:
    item = PROTOTYPE_TRACKS[0].model_copy(deep=True)
    assert item.track is not None
    item.track.artists.append(Artist(name="Convidada sintetica"))
    result = song_collection([item], SongFilters(artist="Convidada sintetica"))
    assert result.tracks == [item]
    assert result.artists == ["Banda Aurora", "Convidada sintetica"]


def test_unknown_dates_and_unavailable_tracks_are_not_invented() -> None:
    item = PROTOTYPE_TRACKS[0].model_copy(deep=True)
    assert item.track is not None
    item.track.album = Album(name="Sem data")
    unavailable = SavedTrack(track=None)
    assert song_collection([item, unavailable], SongFilters()).tracks == [item, unavailable]
    assert song_collection([item, unavailable], SongFilters()).years == []
    assert song_collection([item, unavailable], SongFilters(year="2023")).tracks == []
    assert song_collection([item, unavailable], SongFilters(q="Horizonte")).tracks == [item]


@pytest.mark.parametrize(
    "fields",
    [
        {"q": "x" * 201},
        {"artist": "x" * 513},
        {"album": "x" * 513},
        {"year": "202x"},
        {"extra": "value"},
    ],
)
def test_filter_input_is_validated(fields: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        SongFilters.model_validate(fields)
