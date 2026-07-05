"""Pazaryeri API istemci kayıt defteri."""
from .base import MarketplaceClient, MarketplaceError, NotSupported
from .trendyol_api import TrendyolAPI
from .hepsiburada_api import HepsiburadaAPI
from .n11_api import N11API
from .amazon_api import AmazonAPI

CLIENTS: dict[str, type[MarketplaceClient]] = {
    "trendyol": TrendyolAPI,
    "hepsiburada": HepsiburadaAPI,
    "n11": N11API,
    "amazon": AmazonAPI,
}


def client_for(platform: str, creds: dict) -> MarketplaceClient:
    cls = CLIENTS.get(platform)
    if cls is None:
        raise MarketplaceError(f"Bilinmeyen platform: {platform}")
    return cls(creds)


def clients_for_user(user_id: str) -> list[MarketplaceClient]:
    """Kullanıcının bağlı mağazaları için istemciler; hiç bağlantı yoksa
    panel boş kalmasın diye demo Trendyol istemcisi döner."""
    from .. import db
    from ..marketplaces import list_user_connections

    out = []
    for row in list_user_connections(user_id):
        if row["platform"] in CLIENTS:
            out.append(client_for(row["platform"], db.connection_credentials(row)))
    if not out:
        out.append(TrendyolAPI({}))
    return out
