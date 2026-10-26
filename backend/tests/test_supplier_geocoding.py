"""供应商站点坐标：GCJ-02 转换与地址变化时的坐标解析。"""

from decimal import Decimal

import pytest

from app.suppliers import geocoding
from app.suppliers.geocoding import gcj02_to_wgs84, resolve_site_coordinates
from app.suppliers.models import SupplierSite
from app.suppliers.schemas import SiteInput


def _site(
    *,
    address: str = "上海市嘉定区南翔镇新勤路453号",
    latitude: Decimal | None = None,
    longitude: Decimal | None = None,
) -> SiteInput:
    return SiteInput(
        site_name="上海嘉定",
        country_code="CN",
        region="上海市",
        city="上海市",
        district="嘉定区",
        address=address,
        latitude=latitude,
        longitude=longitude,
    )


def _previous(
    *,
    address: str = "上海市嘉定区南翔镇新勤路453号",
    latitude: Decimal | None = Decimal("31.320458"),
    longitude: Decimal | None = Decimal("121.297917"),
) -> SupplierSite:
    return SupplierSite(
        site_name="上海嘉定",
        country_code="CN",
        region="上海市",
        city="上海市",
        district="嘉定区",
        address=address,
        latitude=latitude,
        longitude=longitude,
    )


def test_gcj02_to_wgs84_known_shanghai_offset() -> None:
    longitude, latitude = gcj02_to_wgs84(121.480229, 31.410854)
    assert longitude == pytest.approx(121.475712, abs=1e-5)
    assert latitude == pytest.approx(31.412748, abs=1e-5)


def test_resolve_geocodes_missing_coordinates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        geocoding,
        "geocode_site",
        lambda _site: (Decimal("31.320458"), Decimal("121.297917")),
    )
    site = _site()

    resolve_site_coordinates(site, previous=None, trust_provided=False)

    assert site.latitude == Decimal("31.320458")
    assert site.longitude == Decimal("121.297917")


def test_resolve_reuses_previous_coordinates_when_address_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        geocoding,
        "geocode_site",
        lambda _site: (_ for _ in ()).throw(AssertionError("不应重复地理编码")),
    )
    site = _site()
    previous = _previous()

    resolve_site_coordinates(site, previous=previous, trust_provided=False)

    assert site.latitude == previous.latitude
    assert site.longitude == previous.longitude


def test_resolve_regeocodes_changed_address_even_with_stale_coordinates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        geocoding,
        "geocode_site",
        lambda _site: (Decimal("31.210505"), Decimal("121.398310")),
    )
    site = _site(
        address="上海市长宁区娄山关路523号",
        latitude=Decimal("31.320458"),
        longitude=Decimal("121.297917"),
    )
    previous = _previous()

    resolve_site_coordinates(site, previous=previous, trust_provided=False)

    assert site.latitude == Decimal("31.210505")
    assert site.longitude == Decimal("121.398310")


def test_resolve_trusts_explicit_coordinate_correction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        geocoding,
        "geocode_site",
        lambda _site: (_ for _ in ()).throw(AssertionError("显式新坐标不应被覆盖")),
    )
    site = _site(
        address="上海市长宁区娄山关路523号",
        latitude=Decimal("31.210505"),
        longitude=Decimal("121.398310"),
    )
    previous = _previous()

    resolve_site_coordinates(site, previous=previous, trust_provided=False)

    assert site.latitude == Decimal("31.210505")
    assert site.longitude == Decimal("121.398310")


def test_resolve_clears_stale_coordinates_when_geocode_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(geocoding, "geocode_site", lambda _site: None)
    site = _site(
        address="上海市长宁区娄山关路523号",
        latitude=Decimal("31.320458"),
        longitude=Decimal("121.297917"),
    )
    previous = _previous()

    resolve_site_coordinates(site, previous=previous, trust_provided=False)

    assert site.latitude is None
    assert site.longitude is None
