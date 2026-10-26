import {useEffect, useMemo, useRef, useState} from 'react';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import type {DashboardGeoRiskPoint, DashboardGeoRiskSitePoint} from '../api';
import type {RiskLevel} from '../types';

/**
 * 总览风险地图：Leaflet + Esri 全球底图。
 *
 * 默认视口聚焦东亚并在省级聚合点位间浏览；放大到省界可见级别后，切换到供应商
 * 站点实际坐标。省界图层既是空间参考，也是“显示明细点位”的开关。
 */

const LEVEL_COLORS: Record<RiskLevel, string> = {
  P1: '#ff4d4f',
  P2: '#ffa940',
  P3: '#4da3ff',
  P4: '#7f8ea3',
};

const EAST_ASIA_CENTER: L.LatLngTuple = [30.5, 114];
const EAST_ASIA_ZOOM = 4;
const MIN_ZOOM = 2;
const MAX_ZOOM = 9;
const ZOOM_STEP = 0.25;
const PROVINCE_BOUNDARY_MIN_ZOOM = 5;
const PROVINCE_BOUNDARY_URL = 'https://geo.datav.aliyun.com/areas_v3/bound/100000_full.json';

let provinceBoundaryPromise: Promise<GeoJSON.FeatureCollection> | null = null;

const loadProvinceBoundaries = (): Promise<GeoJSON.FeatureCollection> => {
  if (provinceBoundaryPromise === null) {
    provinceBoundaryPromise = fetch(PROVINCE_BOUNDARY_URL, {
      headers: {Accept: 'application/json'},
    })
      .then((response) => {
        if (!response.ok) throw new Error(`province boundary ${response.status}`);
        return response.json() as Promise<GeoJSON.FeatureCollection>;
      })
      .catch((error: unknown) => {
        provinceBoundaryPromise = null;
        throw error;
      });
  }
  return provinceBoundaryPromise;
};

interface MapMarker {
  readonly key: string;
  readonly kind: 'region' | 'site';
  readonly name: string;
  readonly supplierName: string | null;
  readonly siteName: string | null;
  readonly region: string | null;
  readonly city: string | null;
  readonly address: string | null;
  readonly latitude: number;
  readonly longitude: number;
  readonly alertCount: number;
  readonly supplierCount: number;
  readonly siteCount: number;
  readonly maxLevel: RiskLevel | null;
}

type Selection = {readonly kind: 'marker'; readonly point: MapMarker} | null;

interface OverviewMapProps {
  readonly riskPoints: readonly DashboardGeoRiskPoint[];
  readonly riskSites: readonly DashboardGeoRiskSitePoint[];
  readonly unlocatedCount: number;
  readonly loading?: boolean;
}

const formatCount = (value: number): string => value.toLocaleString('zh-CN');

export const OverviewMap = ({riskPoints, riskSites, unlocatedCount, loading = false}: OverviewMapProps) => {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<L.Map | null>(null);
  const layerRef = useRef<L.LayerGroup | null>(null);
  const boundaryLayerRef = useRef<L.GeoJSON | null>(null);
  const [selection, setSelection] = useState<Selection>(null);
  const [zoom, setZoom] = useState(EAST_ASIA_ZOOM);
  const [boundaryVisible, setBoundaryVisible] = useState(false);
  const [tileError, setTileError] = useState(false);
  const [tilesReady, setTilesReady] = useState(false);

  const activeMarkers = useMemo<MapMarker[]>(() => {
    if (boundaryVisible) {
      return riskSites.map((point) => ({
        key: `site-${point.site_id}`,
        kind: 'site',
        name: point.supplier_name,
        supplierName: point.supplier_name,
        siteName: point.site_name,
        region: point.region,
        city: point.city,
        address: point.address,
        latitude: point.latitude,
        longitude: point.longitude,
        alertCount: point.alert_count,
        supplierCount: 1,
        siteCount: 1,
        maxLevel: point.max_level,
      }));
    }
    return riskPoints.map((point) => ({
      key: `region-${point.country_code ?? ''}-${point.name}`,
      kind: 'region',
      name: point.name,
      supplierName: null,
      siteName: null,
      region: point.region,
      city: point.city,
      address: null,
      latitude: point.latitude,
      longitude: point.longitude,
      alertCount: point.alert_count,
      supplierCount: point.supplier_count,
      siteCount: point.site_count,
      maxLevel: point.max_level,
    }));
  }, [boundaryVisible, riskPoints, riskSites]);

  useEffect(() => {
    const container = containerRef.current;
    if (container === null || mapRef.current !== null) return undefined;

    const map = L.map(container, {
      center: EAST_ASIA_CENTER,
      zoom: EAST_ASIA_ZOOM,
      minZoom: MIN_ZOOM,
      maxZoom: MAX_ZOOM,
      zoomSnap: ZOOM_STEP,
      zoomDelta: ZOOM_STEP,
      wheelPxPerZoomLevel: 110,
      wheelDebounceTime: 20,
      zoomControl: false,
      attributionControl: false,
      worldCopyJump: true,
    });

    const tiles = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}', {
      maxZoom: 16,
      attribution: 'Tiles &copy; Esri &mdash; Esri, HERE, Garmin, &copy; OpenStreetMap contributors',
    });
    tiles.on('tileerror', () => setTileError(true));
    tiles.on('load', () => setTilesReady(true));
    tiles.addTo(map);

    layerRef.current = L.layerGroup().addTo(map);
    map.on('zoomend', () => setZoom(map.getZoom()));
    mapRef.current = map;

    // 瓦片压色由 CSS filter 完成：滤镜作用于瓦片层本身，拖动地图不会出现遮罩边缘。
    // 省界位于瓦片之上、风险点之下。
    map.createPane('overview-boundary');
    const boundaryPane = map.getPane('overview-boundary');
    if (boundaryPane !== undefined) {
      boundaryPane.style.zIndex = '360';
      boundaryPane.style.pointerEvents = 'none';
    }

    // 容器在 flex 布局中首次测量可能为 0，ResizeObserver 与延时校正共同保证
    // 瓦片/点位按真实视口定位，避免地图整体偏出可视区域。
    const observer = typeof ResizeObserver === 'undefined'
      ? null
      : new ResizeObserver(() => map.invalidateSize());
    observer?.observe(container);
    const timer = window.setTimeout(() => map.invalidateSize(), 300);
    return () => {
      window.clearTimeout(timer);
      observer?.disconnect();
      map.remove();
      mapRef.current = null;
      layerRef.current = null;
      boundaryLayerRef.current = null;
    };
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    if (map === null) return undefined;
    const existing = boundaryLayerRef.current;
    if (zoom < PROVINCE_BOUNDARY_MIN_ZOOM) {
      if (existing !== null && map.hasLayer(existing)) map.removeLayer(existing);
      setBoundaryVisible(false);
      return undefined;
    }
    if (existing !== null && map.hasLayer(existing)) {
      setBoundaryVisible(true);
      return undefined;
    }

    let cancelled = false;
    setBoundaryVisible(false);
    void loadProvinceBoundaries()
      .then((data) => {
        if (cancelled || mapRef.current !== map) return;
        if (boundaryLayerRef.current === null) {
          boundaryLayerRef.current = L.geoJSON(data, {
            pane: 'overview-boundary',
            style: {
              color: '#38bdf8',
              weight: 0.9,
              opacity: 0.34,
              fill: false,
            },
            interactive: false,
          });
        }
        const layer = boundaryLayerRef.current;
        if (!map.hasLayer(layer)) map.addLayer(layer);
        setBoundaryVisible(true);
      })
      .catch(() => {
        // 省界是可选叠加层；加载失败时继续使用省级聚合点位。
        if (!cancelled) setBoundaryVisible(false);
      });

    return () => {
      cancelled = true;
    };
  }, [zoom]);

  useEffect(() => {
    const layer = layerRef.current;
    if (layer === null) return;
    layer.clearLayers();

    activeMarkers.forEach((point) => {
      const hasRisk = point.alertCount > 0 && point.maxLevel !== null;
      const level = point.maxLevel ?? 'P4';
      // 空心点用天蓝描边，避免与 P4 的灰色实心点混淆。
      const color = hasRisk ? LEVEL_COLORS[level] : '#7dd3fc';
      const radius = point.kind === 'site'
        ? (hasRisk ? 4.5 + Math.min(4.5, Math.sqrt(Math.max(1, point.alertCount)) * 0.9) : 4.5)
        : 5.5 + Math.min(7.5, Math.sqrt(Math.max(1, point.alertCount)) * 1.2);
      if (hasRisk) {
        // 两圈错峰波纹：从圆点中心向外扩散，主点位始终压在最上层。
        [0, 1].forEach((ringIndex) => {
          L.circleMarker([point.latitude, point.longitude], {
            radius,
            color,
            weight: 1.5,
            fill: false,
            interactive: false,
            opacity: 0.8,
            className: `overview-risk-ripple${ringIndex === 1 ? ' overview-risk-ripple-delayed' : ''}`,
          }).addTo(layer);
        });
      }
      const marker = L.circleMarker([point.latitude, point.longitude], {
        radius,
        color,
        weight: hasRisk ? 1.5 : 1.6,
        fill: hasRisk,
        fillColor: color,
        fillOpacity: hasRisk ? 0.85 : 0,
        opacity: hasRisk ? 1 : 0.95,
        className: hasRisk ? 'overview-risk-marker' : 'overview-supplier-marker',
      });
      marker.on('click', () => setSelection({kind: 'marker', point}));
      marker.addTo(layer);
    });
  }, [activeMarkers]);

  const selected = selection?.kind === 'marker' ? selection.point : null;
  const selectedHasRisk = selected !== null && selected.alertCount > 0 && selected.maxLevel !== null;
  const topRisk = activeMarkers.find((point) => point.alertCount > 0) ?? null;
  const summaryPoint = topRisk ?? activeMarkers[0] ?? null;
  const isEmpty = activeMarkers.length === 0;
  const siteMode = boundaryVisible;

  return (
    <section className="relative flex min-h-0 flex-1 flex-col overflow-hidden rounded-xl border border-sky-400/15 bg-[radial-gradient(ellipse_at_50%_45%,rgba(12,74,110,0.32),rgba(4,12,24,0.18)_72%)]">
      <div className="relative z-20 flex items-center justify-between border-b border-sky-400/10 px-3.5 py-2">
        <div className="flex items-baseline gap-2">
          <h2 className="text-[13px] font-bold tracking-[0.12em] text-sky-100">风险地图</h2>
          <span className="text-[9px] tracking-[0.2em] text-sky-500/70">RISK MAP</span>
        </div>
        <div className="flex items-center gap-2">
          {loading && <span className="font-mono text-[10px] text-sky-400/70">同步中…</span>}
          <input
            type="range"
            aria-label="地图缩放"
            min={MIN_ZOOM}
            max={MAX_ZOOM}
            step={ZOOM_STEP}
            value={zoom}
            onChange={(event) => {
              const next = Number(event.target.value);
              setZoom(next);
              mapRef.current?.setZoom(next, {animate: false});
            }}
            className="overview-zoom-slider h-1 w-28 cursor-pointer appearance-none rounded-full bg-sky-400/25 accent-sky-400"
          />
          <button
            type="button"
            aria-label="重置地图"
            onClick={() => mapRef.current?.setView(EAST_ASIA_CENTER, EAST_ASIA_ZOOM)}
            className="rounded px-1.5 py-0.5 text-[9px] text-sky-300/80 hover:bg-sky-400/15"
          >
            重置
          </button>
        </div>
      </div>

      <div className="relative min-h-[300px] flex-1 overflow-hidden">
        <div className="absolute inset-0 z-0">
          <div ref={containerRef} className="overview-leaflet h-full w-full" />
        </div>

        {!tilesReady && !tileError && (
          <div className="pointer-events-none absolute inset-0 z-10 grid place-items-center">
            <p className="rounded-md border border-sky-400/15 bg-[#04111f]/85 px-3 py-1.5 text-[10px] tracking-[0.12em] text-sky-300/75">
              底图加载中…
            </p>
          </div>
        )}

        {tileError && (
          <div className="absolute left-1/2 top-2 z-20 -translate-x-1/2 rounded-md border border-amber-400/30 bg-[#04111f]/90 px-3 py-1 text-[9px] text-amber-200/90 backdrop-blur">
            底图瓦片加载失败；点位仍按经纬度显示，请检查内网是否能访问 server.arcgisonline.com
          </div>
        )}

        <div className="pointer-events-none absolute inset-x-0 top-0 z-10 h-24 bg-gradient-to-b from-sky-400/8 to-transparent overview-scan" />

        <span className="pointer-events-none absolute left-2 top-2 z-10 h-3 w-3 border-l border-t border-sky-400/60" />
        <span className="pointer-events-none absolute right-2 top-2 z-10 h-3 w-3 border-r border-t border-sky-400/60" />
        <span className="pointer-events-none absolute bottom-2 left-2 z-10 h-3 w-3 border-b border-l border-sky-400/60" />

        <div className="absolute bottom-3 right-3 z-20 flex flex-col items-end gap-1.5">
          <div className="h-[68px] w-[160px] overflow-hidden rounded-md border border-sky-400/15 bg-[#04111f]/90 px-2 py-1.5 text-[9px] leading-snug text-sky-200/85 backdrop-blur">
            {selected !== null ? (
              <>
                <p className="mb-0.5 truncate text-[10px] font-bold text-sky-50">{selected.name}</p>
                {siteMode && selected.siteName !== null && (
                  <p className="truncate text-[8px] text-sky-400/70">{selected.siteName}</p>
                )}
                <p>
                  {selectedHasRisk ? (
                    <>
                      最高 <span style={{color: LEVEL_COLORS[selected.maxLevel ?? 'P4']}}>{selected.maxLevel}</span>
                      {' · '}提醒 {formatCount(selected.alertCount)} 条
                    </>
                  ) : (
                    <span className="text-sky-400/70">暂无命中风险</span>
                  )}
                </p>
                <p className="truncate text-sky-400/70" title={selected.address ?? undefined}>
                  {selected.city ?? selected.region ?? (siteMode ? '站点' : '地区')}
                  {!siteMode && ` · 供应商 ${formatCount(selected.supplierCount)} 家`}
                </p>
              </>
            ) : summaryPoint !== null ? (
              <>
                <p className="mb-0.5 text-[8px] tracking-[0.14em] text-sky-400/70">
                  {siteMode ? '当前最高风险站点' : '当前最高风险地区'}
                </p>
                <p className="mb-0.5 truncate text-[10px] font-bold text-sky-50">{summaryPoint.name}</p>
                <p className="truncate">
                  {`提醒 ${formatCount(summaryPoint.alertCount)} 条 · ${summaryPoint.city ?? summaryPoint.region ?? ''}`}
                </p>
                <p className="text-sky-400/70">点击地图点位查看详情</p>
              </>
            ) : (
              <p className="text-sky-300/70">{loading ? '正在加载地理数据…' : siteMode ? '暂无供应商站点数据' : '暂无供应商地区数据'}</p>
            )}
          </div>

          <div
            className="flex w-[160px] items-center justify-center gap-1.5 rounded-md border border-sky-400/15 bg-[#04111f]/90 px-1.5 py-1 text-[8px] text-sky-200/80 backdrop-blur"
            title={
              siteMode
                ? `颜色=最高风险等级，大小=提醒数；空心=站点暂无命中风险；另有 ${formatCount(unlocatedCount)} 条提醒的供应商无站点坐标`
                : '颜色=最高风险等级，大小=提醒数；空心=有供应商但暂无命中风险；放大后切换为站点明细'
            }
          >
            <span className="flex items-center gap-1" title="P1 严重"><i className="h-1.5 w-1.5 rounded-full bg-[#ff4d4f]" />P1</span>
            <span className="flex items-center gap-1" title="P2 高风险"><i className="h-1.5 w-1.5 rounded-full bg-[#ffa940]" />P2</span>
            <span className="flex items-center gap-1" title="P3 中度"><i className="h-1.5 w-1.5 rounded-full bg-[#4da3ff]" />P3</span>
            <span className="flex items-center gap-1" title="P4 轻微关注"><i className="h-1.5 w-1.5 rounded-full bg-[#7f8ea3]" />P4</span>
            <span className="flex items-center gap-1" title="暂无命中风险"><i className="h-1.5 w-1.5 rounded-full border border-sky-300/80" />无风险</span>
          </div>
        </div>

        {isEmpty && !loading && (
          <div className="pointer-events-none absolute inset-0 z-10 grid place-items-center">
            <p className="rounded-lg border border-sky-400/15 bg-[#04111f]/75 px-4 py-3 text-[12px] text-sky-200/70">
              {siteMode ? '暂无供应商站点数据' : '暂无供应商地区数据'}；风险提醒与信源健康仍可在右侧查看。
            </p>
          </div>
        )}
      </div>
    </section>
  );
};
