import {afterEach, describe, expect, it, vi} from 'vitest';
import {SupplierImportError, supplierImportApi} from './supplierImportApi';

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('供应商 Excel 导入 API', () => {
  it('下载模板时携带会话并返回二进制文件', async () => {
    const template = new Blob(['template']);
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      blob: async () => template,
    });
    vi.stubGlobal('fetch', fetchMock);

    await expect(supplierImportApi.downloadTemplate()).resolves.toBe(template);
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/suppliers/import-template',
      expect.objectContaining({credentials: 'include'}),
    );
  });

  it('上传时使用 file 字段、自动 CSRF 且不手工设置 multipart Content-Type', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({created_suppliers: 2, updated_suppliers: 1, aliases: 3, sites: 4, products: 5}),
    });
    vi.stubGlobal('fetch', fetchMock);
    vi.stubGlobal('document', {cookie: 'srm_session_csrf=csrf-import'});
    const file = new File(['xlsx'], 'suppliers.xlsx');

    await expect(supplierImportApi.upload(file)).resolves.toEqual({
      createdSuppliers: 2,
      updatedSuppliers: 1,
      aliases: 3,
      sites: 4,
      products: 5,
    });

    const [, options] = fetchMock.mock.calls[0] as [string, RequestInit];
    const body = options.body instanceof FormData ? options.body : null;
    expect(options.method).toBe('POST');
    expect(body?.get('file')).toBe(file);
    expect(new Headers(options.headers).get('X-CSRF-Token')).toBe('csrf-import');
    expect(new Headers(options.headers).has('Content-Type')).toBe(false);
  });

  it('把 422 的 detail.errors 解析为可展示的逐行错误', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: false,
      status: 422,
      json: async () => ({
        detail: {errors: [{sheet: '供应商', row: 3, field: '供应商编码', message: '不能为空'}]},
      }),
    }));

    const upload = supplierImportApi.upload(new File(['xlsx'], 'invalid.xlsx'));

    await expect(upload).rejects.toEqual(new SupplierImportError(422, '导入文件校验失败', [
      {sheet: '供应商', row: 3, field: '供应商编码', message: '不能为空'},
    ]));
  });

  it('保留 409 冲突的服务端说明', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: false,
      status: 409,
      json: async () => ({detail: '供应商编码、注册编号、地点或产品与已有数据冲突'}),
    }));

    await expect(supplierImportApi.upload(new File(['xlsx'], 'conflict.xlsx')))
      .rejects.toMatchObject({status: 409, message: '供应商编码、注册编号、地点或产品与已有数据冲突'});
  });
});
