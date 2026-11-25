// @vitest-environment jsdom
import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import ResourceImage from './ResourceImage'

const state = vi.hoisted(() => ({ userId: 'student-a', view: vi.fn() }))
vi.mock('../../stores/authStore', () => ({
  useAuthStore: (select: (auth: { user: { id: string } }) => unknown) => select({ user: { id: state.userId } }),
}))
vi.mock('../../services/api/storage', () => ({ storageService: { viewResourceImage: state.view } }))

const id = '0123456789abcdef01234567'
const historicalUrl = `http://old-host.example/api/v1/storage/resources/${id}/view`

beforeEach(() => {
  state.userId = 'student-a'
  state.view.mockReset()
  vi.stubGlobal('URL', Object.assign(class extends URL {}, {
    createObjectURL: vi.fn().mockReturnValue('blob:test-image'),
    revokeObjectURL: vi.fn(),
  }))
})
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

it('loads an old shared image by ID without requesting the old host', async () => {
  state.view.mockResolvedValue(new Blob(['test'], { type: 'image/png' }))
  const { unmount } = render(<ResourceImage src={historicalUrl} alt="Shared image" />)
  const image = screen.getByRole('img', { name: 'Shared image' })
  expect(image.getAttribute('src')).toBe(null)
  expect(state.view).toHaveBeenCalledWith(id, expect.any(AbortSignal))
  await waitFor(() => expect(image.getAttribute('src')).toBe('blob:test-image'))
  unmount()
  expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:test-image')
})

it('clears the previous account image even when the URL did not change', async () => {
  state.view.mockResolvedValueOnce(new Blob(['test'])).mockRejectedValueOnce(new Error('Forbidden'))
  const { rerender } = render(<ResourceImage src={historicalUrl} alt="Shared image" />)
  const image = screen.getByRole('img', { name: 'Shared image' })
  await waitFor(() => expect(image.getAttribute('src')).toBe('blob:test-image'))
  state.userId = 'student-b'
  rerender(<ResourceImage src={historicalUrl} alt="Shared image" />)
  expect(image.getAttribute('src')).toBe(null)
  expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:test-image')
  expect(await screen.findByRole('img', { name: /无法查看/ })).toBeTruthy()
})

it('leaves an ordinary image alone and ignores stale responses after replacement', async () => {
  let resolve!: (blob: Blob) => void
  state.view.mockReturnValueOnce(new Promise<Blob>(done => { resolve = done }))
  const { rerender } = render(<ResourceImage src={historicalUrl} alt="Image" />)
  const signal = state.view.mock.calls[0][1] as AbortSignal
  rerender(<ResourceImage src="https://example.org/ordinary.png" alt="Image" />)
  expect(signal.aborted).toBe(true)
  await act(async () => resolve(new Blob(['old'])))
  expect(state.view).toHaveBeenCalledTimes(1)
  expect(screen.getByRole('img').getAttribute('src')).toBe('https://example.org/ordinary.png')
  expect(URL.createObjectURL).not.toHaveBeenCalled()
})
