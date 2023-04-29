import string
import subprocess
from ..exceptions import *
import requests.adapters
from ..otsconfig import config
import requests
import json
import music_tag
import os
from pathlib import Path
import re
from PIL import Image
from io import BytesIO
from ..runtimedata import get_logger, rt_cache
from librespot.audio.decoders import AudioQuality

logger = get_logger("spotutils")
requests.adapters.DEFAULT_RETRIES = 10

def get_artist_albums(session, artist_id):
    logger.info(f"Get albums for artist by id '{artist_id}'")
    access_token = session.tokens().get("user-read-email")
    headers = {'Authorization': f'Bearer {access_token}'}
    resp = requests.get(
        f'https://api.spotify.com/v1/artists/{artist_id}/albums', headers=headers).json()
    return [resp['items'][i]['id'] for i in range(len(resp['items']))]


def get_playlist_data(session, playlist_id):
    logger.info(f"Get playlist dump for '{playlist_id}'")
    access_token = session.tokens().get("user-read-email")
    headers = {'Authorization': f'Bearer {access_token}'}
    resp = requests.get(
        f'https://api.spotify.com/v1/playlists/{playlist_id}', headers=headers).json()
    return resp['name'], resp['owner'], resp['description'], resp['external_urls']['spotify']


def get_track_lyrics(session, track_id, forced_synced):
    lyrics = []
    try:
        params = 'format=json&market=from_token'
        access_token = session.tokens().get("user-read-email")
        headers = {'Authorization': f'Bearer {access_token}'}
        lyrics_json_req = requests.get(
            f'https://spclient.wg.spotify.com/lyrics/v1/track/{track_id}',
            params=params,
            headers=headers
        )
        if lyrics_json_req.status_code == 200:
            lyrics_json = lyrics_json_req.json()
            lyrics.append(f'[au:{lyrics_json["provider"]}]')
            lyrics.append('[by:casualsnek-onTheSpot]')
            if lyrics_json['kind'].lower() == 'text':
                # It's un synced lyrics, if not forcing synced lyrics return it
                if not forced_synced:
                    lyrics = [line['words'][0]['string'] for line in lyrics_json['lines']]
            elif lyrics_json['kind'].lower() == 'line':
                for line in lyrics_json['lines']:
                    minutes, seconds = divmod(line['time'] / 1000, 60)
                    lyrics.append(f'[{minutes:0>2.0f}:{seconds:05.2f}] {line["words"][0]["string"]}')
        else:
            logger.warning(f'Failed to get lyrics for track id: {track_id}, '
                           f'statucode: {lyrics_json_req.status_code}, Text: {lyrics_json_req.text}')
    except (KeyError, IndexError):
        logger.error(f'Failed to get lyrics for track id: {track_id}, ')
    return None if len(lyrics) <= 2 else '\n'.join(lyrics)


def get_tracks_from_playlist(session, playlist_id):
    logger.info(f"Get tracks from playlist by id '{playlist_id}'")
    songs = []
    offset = 0
    limit = 100
    access_token = session.tokens().get("user-read-email")
    headers = {'Authorization': f'Bearer {access_token}'}
    while True:
        params = {'limit': limit, 'offset': offset}
        resp = requests.get(
            f'https://api.spotify.com/v1/playlists/{playlist_id}/tracks', headers=headers, params=params).json()
        offset += limit
        songs.extend(resp['items'])

        if len(resp['items']) < limit:
            break

    return songs



def sanitize_data(value, allow_path_separators=False, escape_quotes=False):
    sanitize = ['*', '?', '\'', '<', '>', '"', '/'] if os.name == 'nt' else []
    if not allow_path_separators:
        sanitize.append(os.path.sep)
    for i in sanitize:
        value = value.replace(i, '')
    if os.name == 'nt':
        value = value.replace('|', '-')
        if value[0].lower() in string.ascii_lowercase and value[1] == ":" and value[2] == '\\':
            # Beginning of win path like C:\, skip first column then remove all other colons
            value = value[:2] + value[2:].replace(':', '-')
        else:
            value = value.replace(':', '-')
        value = value.rstrip('.')
    else:
        if escape_quotes and '"' in value:
            # Since convert uses double quotes, we may need to escape if it exists in path, on windows double quotes is
            # not allowed in path and will be removed
            value = value.replace('"', '\\"')
    return value


def get_album_name(session, album_id):
    logger.info(f"Get album info from album by id ''{album_id}'")
    access_token = session.tokens().get("user-read-email")
    headers = {'Authorization': f'Bearer {access_token}'}
    resp = requests.get(
        f'https://api.spotify.com/v1/albums/{album_id}', headers=headers).json()
    if m := re.search(r'(\d{4})', resp['release_date']):
        return resp['artists'][0]['name'], m.group(1), sanitize_data(resp['name']), resp['total_tracks']
    else:
        return resp['artists'][0]['name'], resp['release_date'], sanitize_data(resp['name']), resp['total_tracks']


def get_album_tracks(session, album_id):
    logger.info(f"Get tracks from album by id '{album_id}'")
    access_token = session.tokens().get("user-read-email")
    songs = []
    offset = 0
    limit = 50
    include_groups = 'album,compilation'

    while True:
        headers = {'Authorization': f'Bearer {access_token}'}
        params = {'limit': limit, 'include_groups': include_groups, 'offset': offset}
        resp = requests.get(
            f'https://api.spotify.com/v1/albums/{album_id}/tracks', headers=headers, params=params).json()
        offset += limit
        songs.extend(resp['items'])

        if len(resp['items']) < limit:
            break
    return songs


def convert_audio_format(filename, quality):
    if os.path.isfile(os.path.abspath(filename)):
        target_path = Path(filename)
        bitrate = "320k" if quality == AudioQuality.VERY_HIGH else "160k"
        temp_name = os.path.join(target_path.parent, ".~"+target_path.stem+".ogg")
        if os.path.isfile(temp_name):
            os.remove(temp_name)

        os.rename(filename, temp_name)
        # Prepare default parameters
        command = [
            config.get('_ffmpeg_bin_path'),
            '-i', sanitize_data(temp_name, allow_path_separators=True, escape_quotes=True),
            '-ar', '44100', '-ac', '2', '-b:a', bitrate,
        ] 
        # Add user defined parameters
        for param in config.get('ffmpeg_args'):
            command.append(param)
        # Add output parameter at last
        command.append( sanitize_data(filename, allow_path_separators=True, escape_quotes=True) )
        logger.info(f'Converting media with ffmpeg. Built commandline {command} ')
        subprocess.check_call(command, shell=False)
        os.remove(temp_name)
    else:
        raise FileNotFoundError


def conv_artist_format(artists):
    formatted = ""
    for artist in artists:
        formatted += artist + config.get('metadata_seperator')+" "
    return formatted[:-2].strip()


def set_audio_tags(filename, metadata, track_id_str):
    logger.info(
        f"Setting tags for audio media at '{filename}', mediainfo -> '{metadata}'")
    type_ = 'track'
    tags = music_tag.load_file(filename)
    for key in metadata.keys():
        value = metadata[key]
        if key == 'artists':
            tags['artist'] = conv_artist_format(value)
        elif key in ['name', 'track_title', 'tracktitle']:
            tags['tracktitle'] = value
        elif key in ['album_name', 'album']:
            tags['album'] = value
        elif key in ['year', 'release_year']:
            tags['year'] = value
        elif key in ['discnumber', 'disc_number', 'disknumber', 'disk_number']:
            tags['discnumber'] = value
        elif key in ['track_number', 'tracknumber']:
            tags['tracknumber'] = value
        elif key == 'lyrics':
            tags['lyrics'] = value
        elif key == 'genre':
            if 'Podcast' in value or 'podcast' in value:
                type_ = 'episode'
            tags['genre'] = conv_artist_format(value)
        elif key in ['total_tracks', 'totaltracks']:
            tags['totaltracks'] = value
        elif key in ['total_discs', 'totaldiscs', 'total_disks', 'totaldisks']:
            tags['totaldiscs'] = value
        elif key == 'isrc':
            tags['isrc'] = value
    tags['comment'] = f'id[spotify.com:{type_}:{track_id_str}]'
    tags.save()


def set_music_thumbnail(filename, image_url):
    logger.info(f"Set thumbnail for audio media at '{filename}' with '{image_url}'")
    img = Image.open(BytesIO(requests.get(image_url).content))
    buf = BytesIO()
    img.save(buf, format='png')
    buf.seek(0)
    tags = music_tag.load_file(filename)
    tags['artwork'] = buf.read()
    tags.save()


def search_by_term(session, search_term, max_results=20, content_types=None) -> dict:
    results = {
        "tracks": [],
        "albums": [],
        "playlists": [],
        "artists": [],
    }
    logger.info(f"Get search result for term '{search_term}', max items '{max_results}'")
    if search_term.strip() == "":
        logger.warning(f"Returning empty data as query is empty !")
        return results
    if content_types is None:
        content_types = ["track", "album", "playlist", "artist"]
    token = session.tokens().get("user-read-email")
    resp = requests.get(
        "https://api.spotify.com/v1/search",
        {
            "limit": max_results,
            "offset": "0",
            "q": search_term,
            "type": ",".join(c_type for c_type in content_types)
        },
        headers={"Authorization": "Bearer %s" % token},
    )
    for c_type in content_types:
        results[c_type + "s"] = resp.json()[c_type + "s"]["items"]
    if len(results["tracks"]) + len(results["albums"]) + len(results["artists"]) + len(results["playlists"]) == 0:
        logger.warning(f"No results for term '{search_term}', max items '{max_results}'")
        raise EmptySearchResultException("No result found for search term '{}' ".format(search_term))
    else:
        return results


def check_premium(session):
    return bool((session.get_user_attribute("type") == "premium") or config.get("force_premium"))


def get_song_info(session, song_id):
    token = session.tokens().get("user-read-email")
    uri = 'https://api.spotify.com/v1/tracks?ids=' + song_id + '&market=from_token'
    info = json.loads(requests.get(uri, headers={"Authorization": "Bearer %s" % token}).text)
    album_url = info['tracks'][0]['album']['href']
    artist_url = info['tracks'][0]['artists'][0]['href']
    if album_url not in rt_cache['REQurl']:
        rt_cache['REQurl'][album_url] = json.loads(
            requests.get(album_url, headers={"Authorization": "Bearer %s" % token}).text
        )
    if artist_url not in rt_cache['REQurl']:
        rt_cache['REQurl'][artist_url] = json.loads(
            requests.get(artist_url, headers={"Authorization": "Bearer %s" % token}).text
        )
    artists = []
    for data in info['tracks'][0]['artists']:
        artists.append(sanitize_data(data['name']))
    info = {
        'artists': artists,
        'album_name': sanitize_data(info['tracks'][0]['album']["name"]),
        'name': sanitize_data(info['tracks'][0]['name']),
        'image_url': get_thumbnail(info['tracks'][0]['album']['images'], preferred_size=640000),
        'release_year': info['tracks'][0]['album']['release_date'].split("-")[0],
        'disc_number': info['tracks'][0]['disc_number'],
        'track_number': info['tracks'][0]['track_number'],
        'total_tracks': info['tracks'][0]['album']['total_tracks'],
        'total_discs': sorted([trk['disc_number'] for trk in rt_cache['REQurl'][album_url]['tracks']['items']])[-1],
        'scraped_song_id': info['tracks'][0]['id'],
        'is_playable': info['tracks'][0]['is_playable'],
        'popularity': info['tracks'][0]['popularity'],
        'isrc': info['tracks'][0]['external_ids'].get('isrc', ''),
        'genre': rt_cache['REQurl'][artist_url]['genres'],
        # https://developer.spotify.com/documentation/web-api/reference/get-track
        # List of genre is supposed to be here, genre from album API is deprecated and it always seems to be unavailable
        # Use artist endpoint to get artist's genre instead
        'label': sanitize_data(rt_cache['REQurl'][album_url]['label']),
        'copyrights':  [ sanitize_data(holder['text']) for holder in rt_cache['REQurl'][album_url]['copyrights'] ],
        'explicit': info['tracks'][0]['explicit']
    }
    return info


def get_episode_info(session, episode_id_str):
    logger.info(f"Get episode info for episode by id '{episode_id_str}'")
    token = session.tokens().get("user-read-email")
    info = json.loads(requests.get("https://api.spotify.com/v1/episodes/" +
                                   episode_id_str, headers={"Authorization": "Bearer %s" % token}).text)
    if "error" in info:
        return None, None, None
    else:
        return sanitize_data(info["show"]["name"]), sanitize_data(info["name"]), get_thumbnail(info['images']), info['release_date'], info['show']['total_episodes'], sanitize_data(info['show']['publisher'])


def get_show_episodes(session, show_id_str):
    logger.info(f"Get episodes for show by id '{show_id_str}'")
    access_token = session.tokens().get("user-read-email")
    episodes = []
    offset = 0
    limit = 50

    while True:
        headers = {'Authorization': f'Bearer {access_token}'}
        params = {'limit': limit, 'offset': offset}
        resp = requests.get(
            f'https://api.spotify.com/v1/shows/{show_id_str}/episodes', headers=headers, params=params).json()
        offset += limit
        for episode in resp["items"]:
            episodes.append(episode["id"])

        if len(resp['items']) < limit:
            break

    return episodes


def get_thumbnail(image_dict, preferred_size=22500):
    images = {}
    for image in image_dict:
        try:
            images[image['height'] * image['width']] = image['url']
        except TypeError:
            # Some playlist and media item do not have cover images
            pass
    available_sizes = sorted(images)
    for size in available_sizes:
        if size >= preferred_size:
            return images[size]
    return images[available_sizes[-1]] if len(available_sizes) > 0 else ""
