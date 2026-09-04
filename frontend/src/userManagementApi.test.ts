import {describe, expect, it, vi} from 'vitest';
import {api, ApiError, type UserRole} from './api';

const FAKE_USER = {id: 1, username: 'analyst01', email: null, display_name: null, role: 'risk_analyst' as UserRole, status: 'active' as const, last_login_at: null, created_at: '2026-01-01T00:00:00Z'};

describe('用户管理 API 请求契约', () => {
  it('listUsers 使用 GET 读取用户列表', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ok: true, status: 200, json: async () => [FAKE_USER]});
    vi.stubGlobal('fetch', fetchMock);

    const users = await api.auth.listUsers();

    expect(fetchMock).toHaveBeenCalledWith('/api/v1/auth/users', expect.objectContaining({credentials: 'include'}));
    expect(fetchMock.mock.calls[0]?.[1]?.method ?? 'GET').toBe('GET');
    expect(users).toEqual([FAKE_USER]);
    vi.unstubAllGlobals();
  });

  it('createUser 使用 POST 并携带 JSON 载荷与 CSRF', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ok: true, status: 201, json: async () => FAKE_USER});
    vi.stubGlobal('fetch', fetchMock);
    vi.stubGlobal('document', {cookie: 'srm_session_csrf=csrf-create'});

    const result = await api.auth.createUser({username: 'analyst01', password: 'P@ssw0rd!', role: 'risk_analyst'});

    const [url, options] = fetchMock.mock.calls[0] as [string, RequestInit];
    const headers = new Headers(options.headers);
    expect(url).toBe('/api/v1/auth/users');
    expect(options.method).toBe('POST');
    expect(headers.get('Content-Type')).toBe('application/json');
    expect(headers.get('X-CSRF-Token')).toBe('csrf-create');
    expect(options.credentials).toBe('include');
    expect(JSON.parse(String(options.body))).toEqual({username: 'analyst01', password: 'P@ssw0rd!', role: 'risk_analyst'});
    expect(result).toEqual(FAKE_USER);
    vi.unstubAllGlobals();
  });

  it('createUser 传播 409 冲突错误', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ok: false, status: 409, json: async () => ({detail: '用户名已存在'})});
    vi.stubGlobal('fetch', fetchMock);
    vi.stubGlobal('document', {cookie: 'srm_session_csrf=csrf-create'});

    await expect(api.auth.createUser({username: 'dup_user', password: 'P@ssw0rd!'}))
      .rejects.toThrow(new ApiError(409, '用户名已存在'));
    vi.unstubAllGlobals();
  });

  it('updateUser 使用 PATCH 并携带 JSON 载荷与 CSRF', async () => {
    const updated = {...FAKE_USER, role: 'risk_admin' as UserRole};
    const fetchMock = vi.fn().mockResolvedValue({ok: true, status: 200, json: async () => updated});
    vi.stubGlobal('fetch', fetchMock);
    vi.stubGlobal('document', {cookie: 'srm_session_csrf=csrf-update'});

    const result = await api.auth.updateUser(1, {role: 'risk_admin'});

    const [url, options] = fetchMock.mock.calls[0] as [string, RequestInit];
    const headers = new Headers(options.headers);
    expect(url).toBe('/api/v1/auth/users/1');
    expect(options.method).toBe('PATCH');
    expect(headers.get('Content-Type')).toBe('application/json');
    expect(headers.get('X-CSRF-Token')).toBe('csrf-update');
    expect(options.credentials).toBe('include');
    expect(JSON.parse(String(options.body))).toEqual({role: 'risk_admin'});
    expect(result).toEqual(updated);
    vi.unstubAllGlobals();
  });

  it('resetPassword 使用 POST 并携带 JSON 载荷与 CSRF', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ok: true, status: 200, json: async () => ({detail: '密码已重置'})});
    vi.stubGlobal('fetch', fetchMock);
    vi.stubGlobal('document', {cookie: 'srm_session_csrf=csrf-reset'});

    const result = await api.auth.resetPassword(42, {new_password: 'N3wP@ss!'});

    const [url, options] = fetchMock.mock.calls[0] as [string, RequestInit];
    const headers = new Headers(options.headers);
    expect(url).toBe('/api/v1/auth/users/42/password-reset');
    expect(options.method).toBe('POST');
    expect(headers.get('Content-Type')).toBe('application/json');
    expect(headers.get('X-CSRF-Token')).toBe('csrf-reset');
    expect(options.credentials).toBe('include');
    expect(JSON.parse(String(options.body))).toEqual({new_password: 'N3wP@ss!'});
    expect(result).toEqual({detail: '密码已重置'});
    vi.unstubAllGlobals();
  });

  it('resetPassword 传播 403 禁止错误', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ok: false, status: 403, json: async () => ({detail: '请使用本人密码修改接口'})});
    vi.stubGlobal('fetch', fetchMock);
    vi.stubGlobal('document', {cookie: 'srm_session_csrf=csrf-reset'});

    await expect(api.auth.resetPassword(1, {new_password: 'N3wP@ss!'}))
      .rejects.toThrow(new ApiError(403, '请使用本人密码修改接口'));
    vi.unstubAllGlobals();
  });

  it('createUser 传播 422 验证错误并提取可读 detail 消息', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ok: false, status: 422, json: async () => ({detail: [{loc: ['body', 'password'], msg: 'String should have at least 8 characters', type: 'string_too_short'}]})});
    vi.stubGlobal('fetch', fetchMock);
    vi.stubGlobal('document', {cookie: 'srm_session_csrf=csrf-create'});

    try {
      await api.auth.createUser({username: 'newuser', password: 'short'});
      expect.unreachable('应抛出 ApiError');
    } catch (error) {
      expect(error).toBeInstanceOf(ApiError);
      expect((error as ApiError).status).toBe(422);
      expect((error as ApiError).message).toBe('String should have at least 8 characters');
    }
    vi.unstubAllGlobals();
  });

  it('createUser 传播多字段 422 验证错误并连接多个可读消息', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false,
      status: 422,
      json: async () => ({detail: [
        {loc: ['body', 'username'], msg: 'field required', type: 'value_error.missing'},
        {loc: ['body', 'password'], msg: 'ensure this value has at least 8 characters', type: 'value_error'},
      ]}),
    });
    vi.stubGlobal('fetch', fetchMock);
    vi.stubGlobal('document', {cookie: 'srm_session_csrf=csrf-create'});

    try {
      await api.auth.createUser({username: '', password: 'short'});
      expect.unreachable('应抛出 ApiError');
    } catch (error) {
      expect(error).toBeInstanceOf(ApiError);
      expect((error as ApiError).status).toBe(422);
      expect((error as ApiError).message).toBe('field required；ensure this value has at least 8 characters');
    }
    vi.unstubAllGlobals();
  });

  it('detail 无 msg 字段时回退为 HTTP 状态码消息', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ok: false, status: 422, json: async () => ({detail: [{loc: ['body', 'name']}]})});
    vi.stubGlobal('fetch', fetchMock);

    try {
      await api.auth.createUser({username: '', password: 'P@ssw0rd!'});
      expect.unreachable('应抛出 ApiError');
    } catch (error) {
      expect(error).toBeInstanceOf(ApiError);
      expect((error as ApiError).message).toBe('HTTP 422');
    }
    vi.unstubAllGlobals();
  });
});
