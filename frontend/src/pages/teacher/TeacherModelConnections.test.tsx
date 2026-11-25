// @vitest-environment jsdom

import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import api from '../../services/api/client'
import TeacherModelConnections from './TeacherModelConnections'

vi.mock('../../services/api/client', () => ({
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}))

const path = '/teacher-model-connections'
const connection = {
  id: 'connection-1',
  name: '课程模型',
  provider: 'openai',
  model: 'gpt-example',
  base_url: 'https://api.openai.com/v1',
  has_api_key: true,
}

const address = () => screen.getByLabelText('API 基础地址')
const keyInput = () => screen.getByLabelText(/^API 密钥/)
const click = (name: string) => fireEvent.click(screen.getByRole('button', { name }))
const fill = (label: string, value: string) => fireEvent.change(screen.getByLabelText(label), { target: { value } })

beforeEach(() => {
  vi.resetAllMocks()
  vi.mocked(api.get).mockResolvedValue({ data: { connections: [connection] } })
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('TeacherModelConnections', () => {
  it('does not open credential forms on an insecure page', async () => {
    vi.stubGlobal('isSecureContext', false)
    render(<TeacherModelConnections onChanged={vi.fn()} />)
    expect(await screen.findByText(/请通过 HTTPS/)).toBeInTheDocument()
    expect(await screen.findByRole('button', { name: '新建连接' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '编辑连接：课程模型' })).toBeDisabled()
    expect(screen.queryByLabelText(/^API 密钥/)).not.toBeInTheDocument()
  })
  it('requires a password on create and saves the fixed provider URL before enabling testing', async () => {
    const onChanged = vi.fn()
    vi.mocked(api.post).mockResolvedValue({ data: {} })
    render(<TeacherModelConnections onChanged={onChanged} />)
    await screen.findByText(connection.name)
    expect(api.get).toHaveBeenCalledWith(path)
    click('新建连接')
    expect(keyInput()).toHaveAttribute('type', 'password')
    expect(keyInput()).toBeRequired()
    expect(address()).toHaveAttribute('readonly')
    expect(address()).toHaveValue('https://api.openai.com/v1')
    fill('服务商', 'siliconflow')
    expect(address()).toHaveValue('https://api.siliconflow.cn/v1')
    fill('服务商', 'deepseek')
    expect(address()).toHaveValue('https://api.deepseek.com')
    expect(screen.getByRole('button', { name: '测试连接' })).toBeDisabled()
    fill('连接名称', ' 课程连接 ')
    fill('模型 ID', ' deepseek-example ')
    click('保存连接')
    expect(api.post).not.toHaveBeenCalled()
    fireEvent.change(keyInput(), { target: { value: 'mock-key' } })
    click('保存连接')
    expect(await screen.findByText('连接已保存。')).toBeInTheDocument()
    expect(api.post).toHaveBeenCalledTimes(1)
    expect(api.post).toHaveBeenCalledWith(path, {
      name: '课程连接', model: 'deepseek-example', provider: 'deepseek',
      api_key: 'mock-key', base_url: 'https://api.deepseek.com',
    })
    expect(onChanged).toHaveBeenCalledTimes(1)
    expect(screen.queryByLabelText(/^API 密钥/)).not.toBeInTheDocument()
    await waitFor(() => expect(screen.getByRole('button', { name: '测试连接' })).toBeEnabled())
  })

  it('rejects non-HTTPS custom URLs and allows a rename without replacing the key or endpoint', async () => {
    const onChanged = vi.fn()
    vi.mocked(api.get).mockResolvedValue({ data: { connections: [
      { ...connection, provider: 'custom', base_url: 'https://example.test/v1' },
    ] } })
    vi.mocked(api.put).mockResolvedValue({ data: {} })
    render(<TeacherModelConnections onChanged={onChanged} />)
    await screen.findByText(connection.name)
    click('新建连接')
    fill('连接名称', '自建模型')
    fill('模型 ID', connection.model)
    fill('服务商', 'custom')
    fireEvent.change(keyInput(), { target: { value: 'sample-key' } })
    fill('API 基础地址', 'http://api.mydomain.com/v1')
    click('保存连接')
    expect(await screen.findByRole('alert')).toHaveTextContent('HTTPS')
    expect(api.post).not.toHaveBeenCalled()
    click('取消')

    click('编辑连接：课程模型')
    expect(keyInput()).toHaveValue('')
    expect(keyInput()).not.toBeRequired()
    expect(screen.getByLabelText('服务商')).toHaveValue('custom')
    expect(screen.getByLabelText('服务商')).toBeDisabled()
    expect(screen.getByLabelText('模型 ID')).toHaveAttribute('readonly')
    expect(address()).toHaveAttribute('readonly')
    expect(address()).toHaveValue('https://example.test/v1')
    fill('连接名称', '重命名课程模型')
    fireEvent.change(keyInput(), { target: { value: '   ' } })
    click('保存连接')
    expect(await screen.findByText('连接已保存。')).toBeInTheDocument()
    expect(api.put).toHaveBeenCalledWith(`${path}/${connection.id}`, {
      name: '重命名课程模型', model: connection.model, provider: 'custom', base_url: 'https://example.test/v1',
    })
    expect(onChanged).toHaveBeenCalledTimes(1)
  })

  it('shows pending, failure and success for a saved connection test without rendering provider messages', async () => {
    const onChanged = vi.fn()
    let finishTest!: (value: { data: { success: boolean; message: string } }) => void
    vi.mocked(api.post)
      .mockImplementationOnce(() => new Promise(resolve => { finishTest = resolve }))
      .mockResolvedValueOnce({ data: { success: true, message: 'mock-sensitive-provider-success' } })
    render(<TeacherModelConnections onChanged={onChanged} />)
    await screen.findByText(connection.name)
    click('测试连接')
    expect(screen.getByRole('status')).toHaveTextContent('正在测试连接')
    expect(screen.getByRole('button', { name: '测试连接' })).toBeDisabled()
    expect(api.post).toHaveBeenCalledWith(`${path}/${connection.id}/test`)
    finishTest({ data: { success: false, message: 'mock-sensitive-provider-error' } })
    expect(await screen.findByRole('alert')).toHaveTextContent('请检查 API 密钥')
    expect(screen.queryByText(/mock-sensitive/)).not.toBeInTheDocument()
    click('测试连接')
    expect(await screen.findByText('连接测试成功。')).toBeInTheDocument()
    expect(screen.queryByText(/mock-sensitive/)).not.toBeInTheDocument()
    expect(onChanged).not.toHaveBeenCalled()
  })

  it('warns about affected groups and only deletes after confirmation', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValueOnce(false).mockReturnValueOnce(true)
    vi.mocked(api.delete).mockResolvedValue({ status: 204 })
    const onChanged = vi.fn()
    render(<TeacherModelConnections onChanged={onChanged} />)
    await screen.findByText(connection.name)
    click('删除连接：课程模型')
    expect(confirm).toHaveBeenCalledWith(expect.stringContaining('小组可能暂时无法使用模型'))
    expect(api.delete).not.toHaveBeenCalled()
    vi.mocked(api.get).mockResolvedValueOnce({ data: { connections: [] } })
    click('删除连接：课程模型')
    expect(await screen.findByText('连接已删除。')).toBeInTheDocument()
    expect(await screen.findByText('尚未配置个人模型连接。')).toBeInTheDocument()
    expect(api.delete).toHaveBeenCalledWith(`${path}/${connection.id}`)
    expect(onChanged).toHaveBeenCalledTimes(1)
  })

  it('recovers from a list error and keeps a failed edit open without displaying raw errors', async () => {
    vi.mocked(api.get).mockRejectedValueOnce(new Error('mock-sensitive-error'))
    vi.mocked(api.put).mockRejectedValueOnce(new Error('mock-sensitive-error'))
    const onChanged = vi.fn()
    render(<TeacherModelConnections onChanged={onChanged} />)
    expect(await screen.findByRole('alert')).toHaveTextContent('无法加载连接列表')
    click('重试加载')
    await screen.findByText(connection.name)
    click('编辑连接：课程模型')
    click('保存连接')
    expect(await screen.findByRole('alert')).toHaveTextContent('保存失败')
    expect(screen.queryByText(/mock-sensitive/)).not.toBeInTheDocument()
    expect(screen.getByLabelText('连接名称')).toHaveValue(connection.name)
    expect(onChanged).not.toHaveBeenCalled()
  })
})
