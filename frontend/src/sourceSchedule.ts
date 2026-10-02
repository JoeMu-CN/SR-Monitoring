import type {DataSource} from './types';

export interface SourceScheduleDisplay {
  readonly label: string;
  readonly title: string;
  readonly ariaLabel: string;
}

// APScheduler 的 day_of_week 语义：0=周一 … 6=周日；具名缩写 mon…sun 同样按周一至周日。
// 注意与标准 crontab 的 0=周日 语义不同，严禁混用。
const WEEKDAY_CHARACTERS: readonly string[] = ['一', '二', '三', '四', '五', '六', '日'];
const NAMED_WEEKDAYS: ReadonlyMap<string, string> = new Map([
  ['mon', '一'],
  ['tue', '二'],
  ['wed', '三'],
  ['thu', '四'],
  ['fri', '五'],
  ['sat', '六'],
  ['sun', '日'],
]);

// 天眼查分片计划固定为每周日 06:00 与每周一 06:00（北京时间），与后端 TYC_SHARD_CRONS 对齐。
const TIANYANCHA_LABEL = '每周日、周一 06:00（分片）';
const TIANYANCHA_DETAIL = '自动核查：每周日 06:00、每周一 06:00 分片执行（北京时间）；也支持按需核查';
const EXTERNAL_TOOL_LABEL = '按需调用';
const EXTERNAL_TOOL_DETAIL = '外部核查工具按需调用，不按信源 cron 定时采集';
const MANUAL_JSON_LABEL = '人工录入';
const MANUAL_JSON_DETAIL = '人工上传文件录入，不联网采集';
const UNSET_LABEL = '未单独配置';
const UNSET_DETAIL =
  '未单独配置 cron；可拉取来源将跟随系统默认周期（默认每 30 分钟，可被部署配置覆盖，北京时间）';
const CUSTOM_LABEL = '自定义周期';
const CUSTOM_PREFIX = '未能识别为常用周期';

const pad2 = (value: number): string => String(value).padStart(2, '0');

const isNumericField = (value: string): boolean => /^\d+$/.test(value);

// 解析 min…max 范围内的纯数字字段；非数字或越界返回 null。
function parseBoundedInteger(value: string, min: number, max: number): number | null {
  if (!isNumericField(value)) {
    return null;
  }
  const parsed = Number.parseInt(value, 10);
  return parsed >= min && parsed <= max ? parsed : null;
}

// 解析 `*/N` 步长字段；未命中或 N 不在 1…59 内（0、60+ 等非法 N）返回 null。
function parseStepField(value: string): number | null {
  const match = /^\*\/(\d+)$/.exec(value);
  if (match === null) {
    return null;
  }
  const step = Number.parseInt(match[1], 10);
  return step >= 1 && step <= 59 ? step : null;
}

// 解析星期字段：数字按 APScheduler 语义 0=周一 … 6=周日（7 及越界值不可识别），
// 具名缩写 mon…sun 大小写不敏感。返回「一」…「日」中的字符或 null。
function resolveWeekday(value: string): string | null {
  const numeric = parseBoundedInteger(value, 0, 6);
  if (numeric !== null) {
    return WEEKDAY_CHARACTERS[numeric] ?? null;
  }
  return NAMED_WEEKDAYS.get(value.toLowerCase()) ?? null;
}

// 将 5 段 cron 转为中文自然语言；无法识别为常用周期时返回 null。
function humanizeCron(fields: readonly string[]): string | null {
  if (fields.length !== 5) {
    return null;
  }
  const minute = fields[0];
  const hour = fields[1];
  const dayOfMonth = fields[2];
  const month = fields[3];
  const dayOfWeek = fields[4];
  if (
    minute === undefined
    || hour === undefined
    || dayOfMonth === undefined
    || month === undefined
    || dayOfWeek === undefined
  ) {
    return null;
  }

  // 每分钟：* * * * *
  if (minute === '*' && hour === '*' && dayOfMonth === '*' && month === '*' && dayOfWeek === '*') {
    return '每分钟';
  }

  // 每 N 分钟：*/N * * * *（1 ≤ N ≤ 59）
  const minuteStep = parseStepField(minute);
  if (minuteStep !== null && hour === '*' && dayOfMonth === '*' && month === '*' && dayOfWeek === '*') {
    return `每 ${minuteStep} 分钟`;
  }

  // 每 N 小时：0 */N * * *（1 ≤ N ≤ 23）
  const hourStep = parseStepField(hour);
  if (
    minute === '0'
    && hourStep !== null
    && hourStep <= 23
    && dayOfMonth === '*'
    && month === '*'
    && dayOfWeek === '*'
  ) {
    return `每 ${hourStep} 小时`;
  }

  const minuteNumber = parseBoundedInteger(minute, 0, 59);
  const hourNumber = parseBoundedInteger(hour, 0, 23);

  // 每小时 N 分：M * * * *（M 为 0…59 数字）
  if (
    hour === '*'
    && dayOfMonth === '*'
    && month === '*'
    && dayOfWeek === '*'
    && minuteNumber !== null
  ) {
    return `每小时 ${minuteNumber} 分`;
  }

  // 以下模式都需要合法的分钟与小时数字。
  if (minuteNumber === null || hourNumber === null) {
    return null;
  }
  const time = `${pad2(hourNumber)}:${pad2(minuteNumber)}`;

  // 每天 HH:MM：M H * * *
  if (dayOfMonth === '*' && month === '*' && dayOfWeek === '*') {
    return `每天 ${time}`;
  }

  // 每周X HH:MM：M H * * D（D 为 APScheduler 语义或 mon…sun）
  if (dayOfMonth === '*' && month === '*' && dayOfWeek !== '*') {
    const weekday = resolveWeekday(dayOfWeek);
    return weekday === null ? null : `每周${weekday} ${time}`;
  }

  // 每月 D 日 HH:MM：M H D * *（1 ≤ D ≤ 31）
  if (dayOfWeek === '*' && month === '*' && dayOfMonth !== '*') {
    const dayNumber = parseBoundedInteger(dayOfMonth, 1, 31);
    return dayNumber === null ? null : `每月 ${dayNumber} 日 ${time}`;
  }

  return null;
}

// 把信息源的调度配置转成中文展示信息：label 为短标签，title/ariaLabel 为完整说明。
// 纯函数：不访问网络、环境变量或全局状态。
export function describeSourceSchedule(
  source: Pick<DataSource, 'schedule' | 'type' | 'code'>,
): SourceScheduleDisplay {
  // 分支优先级：先判类别，再做 cron 识别。
  if (source.type === 'external_tool' && source.code === 'tianyancha') {
    // 天眼查走固定分片计划，无论 schedule 取何值都不进入通用 cron 识别。
    return {label: TIANYANCHA_LABEL, title: TIANYANCHA_DETAIL, ariaLabel: TIANYANCHA_DETAIL};
  }
  if (source.type === 'external_tool') {
    return {label: EXTERNAL_TOOL_LABEL, title: EXTERNAL_TOOL_DETAIL, ariaLabel: EXTERNAL_TOOL_DETAIL};
  }
  if (source.code === 'manual-json') {
    return {label: MANUAL_JSON_LABEL, title: MANUAL_JSON_DETAIL, ariaLabel: MANUAL_JSON_DETAIL};
  }

  const schedule = source.schedule;
  if (schedule === null || schedule.trim() === '') {
    return {label: UNSET_LABEL, title: UNSET_DETAIL, ariaLabel: UNSET_DETAIL};
  }

  // 归一化：折叠首尾空白与连续空白；识别与 title 均基于规范化后的字段。
  const fields = schedule.trim().split(/\s+/);
  const normalizedCron = fields.join(' ');
  const label = humanizeCron(fields);
  if (label === null) {
    const title = `${CUSTOM_PREFIX}：${normalizedCron}`;
    return {label: CUSTOM_LABEL, title, ariaLabel: `调度周期：${CUSTOM_LABEL}，${title}`};
  }
  return {
    label,
    title: `${normalizedCron}（北京时间）`,
    ariaLabel: `调度周期：${label}，原始 cron ${normalizedCron}（北京时间）`,
  };
}
