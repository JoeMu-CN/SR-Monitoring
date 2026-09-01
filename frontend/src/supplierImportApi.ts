export interface SupplierImportSummary {
  readonly createdSuppliers: number;
  readonly updatedSuppliers: number;
  readonly aliases: number;
  readonly sites: number;
  readonly products: number;
}

export interface SupplierImportIssue {
  readonly sheet: string;
  readonly row?: number;
  readonly field?: string;
  readonly message: string;
}

export class SupplierImportError extends Error {
  constructor(
    public readonly status: number,
    message: string,
    public readonly issues: readonly SupplierImportIssue[] = [],
  ) {
    super(message);
    this.name = 'SupplierImportError';
  }
}

const isRecord = (value: unknown): value is Record<string, unknown> => (
  typeof value === 'object' && value !== null
);

const parseIssue = (value: unknown): SupplierImportIssue | null => {
  if (!isRecord(value) || typeof value.sheet !== 'string' || typeof value.message !== 'string') return null;
  const row = typeof value.row === 'number' ? value.row : undefined;
  const field = typeof value.field === 'string' ? value.field : undefined;
  return {...(row === undefined ? {} : {row}), ...(field === undefined ? {} : {field}), sheet: value.sheet, message: value.message};
};

const parseSummary = (value: unknown): SupplierImportSummary => {
  if (!isRecord(value)) throw new SupplierImportError(502, '导入服务返回了无效响应');
  const fields = ['created_suppliers', 'updated_suppliers', 'aliases', 'sites', 'products'] as const;
  if (!fields.every((field) => typeof value[field] === 'number')) {
    throw new SupplierImportError(502, '导入服务返回了无效响应');
  }
  return {
    createdSuppliers: Number(value.created_suppliers),
    updatedSuppliers: Number(value.updated_suppliers),
    aliases: Number(value.aliases),
    sites: Number(value.sites),
    products: Number(value.products),
  };
};

const parseError = async (response: Response): Promise<SupplierImportError> => {
  let payload: unknown;
  try {
    payload = await response.json();
  } catch (caught) {
    if (caught instanceof SyntaxError) return new SupplierImportError(response.status, `HTTP ${response.status}`);
    throw caught;
  }
  if (!isRecord(payload) || !('detail' in payload)) return new SupplierImportError(response.status, `HTTP ${response.status}`);
  if (typeof payload.detail === 'string') return new SupplierImportError(response.status, payload.detail);
  if (!isRecord(payload.detail) || !Array.isArray(payload.detail.errors)) {
    return new SupplierImportError(response.status, `HTTP ${response.status}`);
  }
  const issues = payload.detail.errors.map(parseIssue).filter((issue): issue is SupplierImportIssue => issue !== null);
  return new SupplierImportError(response.status, '导入文件校验失败', issues);
};

const csrfHeaders = (): Headers => {
  const headers = new Headers();
  if (typeof document === 'undefined') return headers;
  const csrfCookie = document.cookie.split('; ').find((item) => item.split('=', 1)[0].endsWith('_csrf'));
  if (csrfCookie) headers.set('X-CSRF-Token', csrfCookie.split('=').slice(1).join('='));
  return headers;
};

export const supplierImportApi = {
  downloadTemplate: async (): Promise<Blob> => {
    const response = await fetch('/api/v1/suppliers/import-template', {credentials: 'include'});
    if (!response.ok) throw await parseError(response);
    return response.blob();
  },
  upload: async (file: File): Promise<SupplierImportSummary> => {
    const body = new FormData();
    body.append('file', file);
    const response = await fetch('/api/v1/suppliers/import', {
      method: 'POST',
      headers: csrfHeaders(),
      body,
      credentials: 'include',
    });
    if (!response.ok) throw await parseError(response);
    const payload: unknown = await response.json();
    return parseSummary(payload);
  },
};
