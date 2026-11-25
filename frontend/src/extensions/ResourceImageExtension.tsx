import Image from '@tiptap/extension-image'
import { NodeViewWrapper, ReactNodeViewRenderer, type NodeViewProps } from '@tiptap/react'
import ResourceImage from '../components/ui/ResourceImage'

function ImageNode({ node, extension }: NodeViewProps) {
  const { src, alt, title, width, height } = node.attrs
  return (
    <NodeViewWrapper contentEditable={false}>
      <ResourceImage src={src} alt={alt ?? ''} title={title ?? undefined} width={width ?? undefined}
        height={height ?? undefined} loading="lazy" className={extension.options.HTMLAttributes?.class} />
    </NodeViewWrapper>
  )
}

// Preserve the existing image node and saved HTML; blob URLs are display-only.
export const ResourceImageExtension = Image.extend({
  addNodeView() {
    return ReactNodeViewRenderer(ImageNode)
  },
})
