"""供应商站点地理编码与坐标系转换。

数据库统一存储 WGS84。高德 Web 服务只接受中国地址并返回 GCJ-02，因此这里
在写入前转换为 WGS84；海外地址不调用高德，避免产生错误坐标。
"""

from __future__ import annotations

import math
import time
from decimal import Decimal
from functools import lru_cache

import httpx

from app.config import get_amap_settings
from app.suppliers.models import SupplierSite
from app.suppliers.schemas import SiteInput

_AMAP_GEOCODE_URL = "https://restapi.amap.com/v3/geocode/geo"
_AXIS = 6378245.0
_EE = 0.00669342162296594323


def _out_of_china(longitude: float, latitude: float) -> bool:
    return not (72.004 <= longitude <= 137.8347 and 0.8293 <= latitude <= 55.8271)


def _transform_lat(x: float, y: float) -> float:
    value = -100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * x * y
    value += 0.2 * math.sqrt(abs(x))
    value += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
    value += (20.0 * math.sin(y * math.pi) + 40.0 * math.sin(y / 3.0 * math.pi)) * 2.0 / 3.0
    value += (160.0 * math.sin(y / 12.0 * math.pi) + 320 * math.sin(y * math.pi / 30.0)) * 2.0 / 3.0
    return value


def _transform_lng(x: float, y: float) -> float:
    value = 300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y
    value += 0.1 * math.sqrt(abs(x))
    value += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
    value += (20.0 * math.sin(x * math.pi) + 40.0 * math.sin(x / 3.0 * math.pi)) * 2.0 / 3.0
    value += (
        150.0 * math.sin(x / 12.0 * math.pi)
        + 300.0 * math.sin(x / 30.0 * math.pi)
    ) * 2.0 / 3.0
    return value


def gcj02_to_wgs84(longitude: float, latitude: float) -> tuple[float, float]:
    """把高德/腾讯 GCJ-02 坐标转换为 WGS84，返回 (经度, 纬度)。"""
    if _out_of_china(longitude, latitude):
        return longitude, latitude
    delta_lat = _transform_lat(longitude - 105.0, latitude - 35.0)
    delta_lng = _transform_lng(longitude - 105.0, latitude - 35.0)
    radians = latitude / 180.0 * math.pi
    magic = 1 - _EE * math.sin(radians) ** 2
    sqrt_magic = math.sqrt(magic)
    delta_lat = (delta_lat * 180.0) / ((_AXIS * (1 - _EE)) / (magic * sqrt_magic) * math.pi)
    delta_lng = (delta_lng * 180.0) / (_AXIS / sqrt_magic * math.cos(radians) * math.pi)
    return longitude - delta_lng, latitude - delta_lat


@lru_cache(maxsize=1024)
def _geocode_cn_address(address: str, city: str) -> tuple[float, float] | None:
    settings = get_amap_settings()
    if not settings.web_service_key:
        return None

    params = {
        "key": settings.web_service_key,
        "address": address,
        "city": city,
        "output": "JSON",
    }
    for attempt in range(2):
        try:
            response = httpx.get(
                _AMAP_GEOCODE_URL,
                params=params,
                timeout=settings.timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError):
            return None
        geocodes = payload.get("geocodes") or []
        if str(payload.get("status")) == "1" and geocodes:
            location = str(geocodes[0].get("location") or "")
            try:
                longitude_text, latitude_text = location.split(",", 1)
                longitude = float(longitude_text)
                latitude = float(latitude_text)
            except (ValueError, TypeError):
                return None
            return gcj02_to_wgs84(longitude, latitude)
        # 高德默认 QPS 很低；批量导入时遇到限流短暂退避一次。
        if payload.get("info") == "CUQPS_HAS_EXCEEDED_THE_LIMIT" and attempt == 0:
            time.sleep(1.1)
            continue
        return None
    return None


def geocode_site(site: SiteInput) -> tuple[Decimal, Decimal] | None:
    """按中国地址返回 WGS84 的 (纬度, 经度)；海外或失败返回 None。"""
    if site.country_code.upper() != "CN":
        return None
    coords = _geocode_cn_address(site.address.strip(), (site.city or site.region or "").strip())
    if coords is None:
        return None
    longitude, latitude = coords
    return (
        Decimal(str(round(latitude, 6))),
        Decimal(str(round(longitude, 6))),
    )


def _location_key(
    *,
    country_code: str,
    region: str | None,
    city: str | None,
    district: str | None,
    address: str,
) -> tuple[str, str, str, str, str]:
    return (
        country_code.strip().upper(),
        (region or "").strip(),
        (city or "").strip(),
        (district or "").strip(),
        " ".join(address.split()),
    )


def resolve_site_coordinates(
    site: SiteInput,
    *,
    previous: SupplierSite | None = None,
    trust_provided: bool = True,
) -> None:
    """补齐或更新站点坐标。

    - 显式坐标默认为 WGS84；网页编辑时若地址发生变化，旧坐标已失效，强制重编码。
    - 地址未变且未提供新坐标时复用旧坐标，避免无谓的外部请求。
    - 地理编码失败时不阻塞保存；地址已变化则清空旧坐标，避免继续显示错误位置。
    """
    has_coordinates = site.latitude is not None and site.longitude is not None
    address_changed = previous is None or _location_key(
        country_code=site.country_code,
        region=site.region,
        city=site.city,
        district=site.district,
        address=site.address,
    ) != _location_key(
        country_code=previous.country_code,
        region=previous.region,
        city=previous.city,
        district=previous.district,
        address=previous.address,
    )

    coordinates_changed = previous is None or (
        previous.latitude != site.latitude or previous.longitude != site.longitude
    )
    if has_coordinates and (
        trust_provided or previous is None or not address_changed or coordinates_changed
    ):
        return
    if not has_coordinates and previous is not None and not address_changed:
        if previous.latitude is not None and previous.longitude is not None:
            site.latitude = previous.latitude
            site.longitude = previous.longitude
            return

    coordinates = geocode_site(site)
    if coordinates is None:
        if previous is not None and address_changed and not trust_provided:
            site.latitude = None
            site.longitude = None
        return
    site.latitude, site.longitude = coordinates
