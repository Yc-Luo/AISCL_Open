import { useEffect, useRef, useState, type ImgHTMLAttributes } from 'react'
import { getResourceImageId } from '../../lib/resourceImage'
import { storageService } from '../../services/api/storage'
import { useAuthStore } from '../../stores/authStore'

type Props = ImgHTMLAttributes<HTMLImageElement> & { resourceId?: string }

export default function ResourceImage({ src, resourceId, loading, ...props }: Props) {
  const id = getResourceImageId(src, resourceId)
  const userId = useAuthStore(state => state.user?.id)
  const image = useRef<HTMLImageElement>(null)
  const key = id && userId ? `${userId}:${id}` : undefined
  const [seenKey, setSeenKey] = useState<string>()
  const [preview, setPreview] = useState<{ key: string; url?: string }>()

  useEffect(() => {
    if (!key || !image.current || loading !== 'lazy' || typeof IntersectionObserver === 'undefined') return
    const observer = new IntersectionObserver(([entry]) => {
      if (entry?.isIntersecting) {
        setSeenKey(key)
        observer.disconnect()
      }
    }, { rootMargin: '240px' })
    observer.observe(image.current)
    return () => observer.disconnect()
  }, [key, loading])

  const deferred = loading === 'lazy' && typeof IntersectionObserver !== 'undefined' && seenKey !== key
  useEffect(() => {
    if (!key || !id || deferred) return
    const request = new AbortController()
    let objectUrl: string | undefined
    void storageService.viewResourceImage(id, request.signal).then(blob => {
      if (request.signal.aborted) return
      objectUrl = URL.createObjectURL(blob)
      setPreview({ key, url: objectUrl })
    }).catch(() => {
      if (!request.signal.aborted) setPreview({ key })
    })
    return () => {
      request.abort()
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [key, id, deferred])

  if (!id) return <img ref={image} {...props} src={src} loading={loading} />
  const current = preview?.key === key ? preview : undefined
  const alt = current && !current.url ? `${props.alt || '图片'}（无法查看）` : props.alt
  return <img ref={image} {...props} alt={alt} src={current?.url} />
}
