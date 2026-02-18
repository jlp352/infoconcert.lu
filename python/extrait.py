import requests

def get_artist_id(artist_name):
    """Récupère l'ID Deezer de l'artiste"""
    r = requests.get("https://api.deezer.com/search/artist", params={"q": artist_name})
    data = r.json()["data"]
    if not data:
        raise ValueError("Artiste non trouvé")
    return data[0]["id"]

def get_top_tracks(artist_id, top_n=10):
    """Retourne les top_n morceaux les plus populaires d'un artiste"""
    url = f"https://api.deezer.com/artist/{artist_id}/top?limit=50"  # récupère jusqu'à 50 morceaux
    r = requests.get(url)
    tracks = r.json().get("data", [])

    if not tracks:
        return []

    # trier par rank décroissant (popularité réelle)
    tracks_sorted = sorted(tracks, key=lambda x: x["rank"], reverse=True)

    top_tracks = []
    for track in tracks_sorted[:top_n]:
        top_tracks.append({
            "title": track["title"],
            "preview_url": track["preview"],
            "rank": track["rank"],
            "cover": track["album"]["cover_medium"],
            "link": track["link"]
        })

    return top_tracks

if __name__ == "__main__":
    artist_name = "Puggy"  # change selon ton artiste
    artist_id = get_artist_id(artist_name)
    top_10 = get_top_tracks(artist_id, top_n=10)

    print(f"Top 10 morceaux de {artist_name} :\n")
    for i, track in enumerate(top_10, start=1):
        print(f"{i}. {track['title']}")
        print(f"   Preview 30s : {track['preview_url']}")
        print(f"   Rank       : {track['rank']}")
        print(f"   Cover      : {track['cover']}")
        print(f"   Lien Deezer: {track['link']}\n")
