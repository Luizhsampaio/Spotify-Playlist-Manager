import hashlib
from dataclasses import dataclass, field

from music_curator.models import SavedTrack, Track


@dataclass
class ArtistPlaylist:
    key: str
    name: str
    artist_id: str | None
    tracks: list[Track] = field(default_factory=list)


@dataclass
class ArtistPlaylistCollection:
    playlists: list[ArtistPlaylist]
    skipped_tracks: int


def artist_playlists(items: list[SavedTrack]) -> ArtistPlaylistCollection:
    groups: dict[str, ArtistPlaylist] = {}
    seen: dict[str, set[str]] = {}
    skipped = 0
    for item in items:
        track = item.track
        if track is None or track.id is None or not any(a.name.strip() for a in track.artists):
            skipped += 1
            continue
        for artist in track.artists:
            if not artist.name.strip():
                continue
            identity = f"id:{artist.id}" if artist.id else f"name:{artist.name}"
            key = hashlib.sha256(identity.encode()).hexdigest()
            if key not in groups:
                groups[key] = ArtistPlaylist(key, artist.name, artist.id)
                seen[key] = set()
            if track.id not in seen[key]:
                groups[key].tracks.append(track)
                seen[key].add(track.id)
    return ArtistPlaylistCollection(
        sorted(groups.values(), key=lambda group: (group.name.casefold(), group.key)), skipped
    )
