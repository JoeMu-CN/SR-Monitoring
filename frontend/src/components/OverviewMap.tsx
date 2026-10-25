import {useEffect, useRef, useState} from 'react';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import type {DashboardGeoRiskPoint} from '../api';
import type {RiskLevel} from '../types';

/**
 * 总览风险地图：Leaflet + Esri 全球底图。
 *
 * 默认视口聚焦东亚，滚轮/拖拽/按钮可缩放到全球。风险点位按 WGS84 经纬度叠加；
 * 底图为公开瓦片，内网环境需保证可访问 server.arcgisonline.com。
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

type Selection =
  | {readonly kind: 'risk'; readonly point: DashboardGeoRiskPoint}
  | null;

interface OverviewMapProps {
  readonly riskPoints: readonly DashboardGeoRiskPoint[];
  readonly unlocatedCount: number;
  readonly loading?: boolean;
}

const formatCount = (value: number): string => value.toLocaleString('zh-CN');

export const OverviewMap = ({riskPoints, unlocatedCount, loading = false}: OverviewMapProps) => {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<L.Map | null>(null);
  const layerRef = useRef<L.LayerGroup | null>(null);
  const [selection, setSelection] = useState<Selection>(null);
  const [zoom, setZoom] = useState(EAST_ASIA_ZOOM);
  const [tileError, setTileError] = useState(false);
  const [tilesReady, setTilesReady] = useState(false);

  useEffect(() => {
    const container = containerRef.current;
    if (container === null || mapRef.current !== null) return undefined;

    const map = L.map(container, {
      center: EAST_ASIA_CENTER,
      zoom: EAST_ASIA_ZOOM,
      minZoom: MIN_ZOOM,
      maxZoom: MAX_ZOOM,
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

    // 在瓦片之上、点位之下铺一层深蓝压色，让 Esri 灰底融入大屏深色配色。
    map.createPane('overview-tint');
    const tintPane = map.getPane('overview-tint');
    if (tintPane !== undefined) {
      tintPane.style.zIndex = '350';
      tintPane.style.pointerEvents = 'none';
    }
    const tintOptions: L.PolylineOptions = {
      pane: 'overview-tint',
      stroke: false,
      fillColor: '#051a2e',
      fillOpacity: 0.52,
      interactive: false,
    };
    L.rectangle([[-85, -540], [85, -180]], tintOptions).addTo(map);
    L.rectangle([[-85, -180], [85, 180]], tintOptions).addTo(map);
    L.rectangle([[-85, 180], [85, 540]], tintOptions).addTo(map);

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
    };
  }, []);

  useEffect(() => {
    const layer = layerRef.current;
    if (layer === null) return;
    layer.clearLayers();

    riskPoints.forEach((point, index) => {
      const hasRisk = point.alert_count > 0 && point.max_level !== null;
      const level = point.max_level ?? 'P4';
      // 空心点用天蓝描边，避免与 P4 的灰色实心点混淆。
      const color = hasRisk ? LEVEL_COLORS[level] : '#7dd3fc';
      const sizeBase = hasRisk ? point.alert_count : point.supplier_count;
      const radius = 5.5 + Math.min(7.5, Math.sqrt(Math.max(1, sizeBase)) * 1.2);
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
      if (hasRisk && index < 3) {
        marker.bindTooltip(point.name, {
          permanent: true,
          direction: 'right',
          offset: [8, 0],
          className: 'overview-map-label',
        });
      } else {
        marker.bindTooltip(
          hasRisk
            ? `${point.name}：供应商 ${point.supplier_count} 家，提醒 ${point.alert_count} 条，最高 ${point.max_level}`
            : `${point.name}：供应商 ${point.supplier_count} 家，暂无命中风险`,
          {direction: 'top'},
        );
      }
      marker.on('click', () => setSelection({kind: 'risk', point}));
      marker.addTo(layer);
    });
  }, [riskPoints]);

  const selectedRisk = selection?.kind === 'risk' ? selection.point : null;
  const selectedHasRisk = selectedRisk !== null && selectedRisk.alert_count > 0 && selectedRisk.max_level !== null;
  const topRisk = riskPoints.find((point) => point.alert_count > 0) ?? null;
  const summaryPoint = topRisk ?? riskPoints[0] ?? null;
  const isEmpty = riskPoints.length === 0;

  return (
    <section className="relative flex min-h-0 flex-1 flex-col overflow-hidden rounded-xl border border-sky-400/15 bg-[radial-gradient(ellipse_at_50%_45%,rgba(12,74,110,0.32),rgba(4,12,24,0.18)_72%)]">
      <div className="relative z-20 flex items-center justify-between border-b border-sky-400/10 px-3.5 py-2">
        <div className="flex items-baseline gap-2">
          <h2 className="text-[13px] font-bold tracking-[0.12em] text-sky-100">风险地图</h2>
          <span className="text-[9px] tracking-[0.2em] text-sky-500/70">RISK MAP</span>
        </div>
        <div className="flex items-center gap-2">
          <span className="font-mono text-[10px] text-sky-400/70">{loading ? '同步中…' : '实时视图'}</span>
          <div className="flex items-center gap-0.5 rounded border border-sky-400/15 bg-sky-400/5 p-0.5 text-[10px] text-sky-200/80">
            <button type="button" aria-label="缩小地图" onClick={() => mapRef.current?.zoomOut()} className="grid h-5 w-5 place-items-center rounded hover:bg-sky-400/15">−</button>
            <span className="w-8 text-center font-mono text-[9px] text-sky-300/80">{zoom} 级</span>
            <button type="button" aria-label="放大地图" onClick={() => mapRef.current?.zoomIn()} className="grid h-5 w-5 place-items-center rounded hover:bg-sky-400/15">+</button>
            <button type="button" aria-label="重置地图" onClick={() => mapRef.current?.setView(EAST_ASIA_CENTER, EAST_ASIA_ZOOM)} className="rounded px-1.5 py-0.5 text-[9px] hover:bg-sky-400/15">重置</button>
          </div>
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
            {selectedRisk !== null ? (
              <>
                <p className="mb-0.5 truncate text-[10px] font-bold text-sky-50">{selectedRisk.name}</p>
                <p>
                  {selectedHasRisk ? (
                    <>
                      最高 <span style={{color: LEVEL_COLORS[selectedRisk.max_level ?? 'P4']}}>{selectedRisk.max_level}</span>
                      {' · '}提醒 {formatCount(selectedRisk.alert_count)} 条
                    </>
                  ) : (
                    <span className="text-sky-400/70">暂无命中风险</span>
                  )}
                </p>
                <p title="站点指该地区供应商的登记地点数量">
                  供应商 {formatCount(selectedRisk.supplier_count)} 家
                  {selectedRisk.site_count > selectedRisk.supplier_count
                    ? ` · 站点 ${formatCount(selectedRisk.site_count)} 个`
                    : ''}
                </p>
              </>
            ) : summaryPoint !== null ? (
              <>
                <p className="mb-0.5 text-[8px] tracking-[0.14em] text-sky-400/70">
                  {topRisk !== null ? '当前最高风险地区' : '供应商分布'}
                </p>
                <p className="mb-0.5 truncate text-[10px] font-bold text-sky-50">{summaryPoint.name}</p>
                <p>
                  {topRisk !== null
                    ? `提醒 ${formatCount(summaryPoint.alert_count)} 条 · 供应商 ${formatCount(summaryPoint.supplier_count)} 家`
                    : `暂无命中风险 · 供应商 ${formatCount(summaryPoint.supplier_count)} 家`}
                </p>
                <p className="text-sky-400/70">点击地图点位查看详情</p>
              </>
            ) : (
              <p className="text-sky-300/70">{loading ? '正在加载地理数据…' : '暂无供应商地理数据'}</p>
            )}
          </div>

          <div
            className="flex w-[160px] items-center justify-center gap-1.5 rounded-md border border-sky-400/15 bg-[#04111f]/90 px-1.5 py-1 text-[8px] text-sky-200/80 backdrop-blur"
            title={
              unlocatedCount > 0
                ? `颜色=最高风险等级，大小=提醒数；空心=有供应商但暂无命中风险；另有 ${formatCount(unlocatedCount)} 条未定位`
                : '颜色=最高风险等级，大小=提醒数；空心=有供应商但暂无命中风险'
            }
          >
            <span className="flex items-center gap-1" title="P1 严重"><i className="h-1.5 w-1.5 rounded-full bg-[#ff4d4f]" />P1</span>
            <span className="flex items-center gap-1" title="P2 高风险"><i className="h-1.5 w-1.5 rounded-full bg-[#ffa940]" />P2</span>
            <span className="flex items-center gap-1" title="P3 中度"><i className="h-1.5 w-1.5 rounded-full bg-[#4da3ff]" />P3</span>
            <span className="flex items-center gap-1" title="P4 轻微关注"><i className="h-1.5 w-1.5 rounded-full bg-[#7f8ea3]" />P4</span>
            <span className="flex items-center gap-1" title="有供应商但暂无命中风险"><i className="h-1.5 w-1.5 rounded-full border border-sky-300/80" />无风险</span>
          </div>
        </div>

        {isEmpty && !loading && (
          <div className="pointer-events-none absolute inset-0 z-10 grid place-items-center">
            <p className="rounded-lg border border-sky-400/15 bg-[#04111f]/75 px-4 py-3 text-[12px] text-sky-200/70">
              暂无地理点位数据；风险提醒与信源健康仍可在右侧查看。
            </p>
          </div>
        )}
      </div>
    </section>
  );
};
