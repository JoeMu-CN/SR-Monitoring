// —— 展示层工具（只影响页面呈现，不改变后端存储值） ——

// 来源名称展示规范化：去掉半角/全角括号及其内容，避免括号里的技术标识挤占名称位置。
// 总览页与信息源列表页共用同一实现，避免两页对同一名称给出不同展示结果。
export const displaySourceName = (name: string): string => {
  const stripped = name.replace(/[（(][^）)]*[）)]/g, '').replace(/\s+/g, ' ').trim();
  return stripped || name;
};
