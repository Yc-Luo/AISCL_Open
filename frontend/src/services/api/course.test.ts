import { afterEach, expect, it, vi } from 'vitest'
import api from './client'
import { courseService, type LearningDesign } from './course'

vi.mock('./client', () => ({ default: { get: vi.fn(), put: vi.fn() } }))

afterEach(() => { vi.clearAllMocks() })

it('requests the course catalog and exposes only model IDs and names', async () => {
  vi.mocked(api.get).mockResolvedValue({ data: { models: [
    { id: 'model-1', name: 'Model One', api_key: 'private', base_url: 'https://private.example' },
  ] } })

  await expect(courseService.getLearningDesignModels('class/one')).resolves.toEqual([{ id: 'model-1', name: 'Model One' }])
  expect(api.get).toHaveBeenCalledWith('/courses/class%2Fone/learning-design/models')
})

it('preserves the distinction between omitted assignments and an explicit clear on PUT', async () => {
  const design: LearningDesign = { roles: [], orchestration_mode: 'adaptive', stages: [] }
  const clear = { ...design, model_assignments: { default_model_id: null, role_model_ids: {} } }
  const saved = { ...design, revision: 'r1', model_assignments: null }
  vi.mocked(api.put).mockResolvedValue({ data: { design: saved } })

  await expect(courseService.saveLearningDesign('class/one', design)).resolves.toEqual(saved)
  await courseService.saveLearningDesign('class/one', clear)
  expect(api.put).toHaveBeenNthCalledWith(1, '/courses/class%2Fone/learning-design', design)
  expect(api.put).toHaveBeenNthCalledWith(2, '/courses/class%2Fone/learning-design', clear)
})
