import { ChangeEvent, ClipboardEvent, DragEvent, useEffect, useRef, useState } from 'react'
import './anon.css'

const DEFAULT_HEADING = 'NO NAME. NO NONSENSE.'
const DEFAULT_BODY = 'Put your paragraph here. Keep it sharp, keep it honest, and leave the polished corporate rubbish somewhere else.'

function AnonPage() {
  const isEditor = window.location.pathname === '/anon/edit'
  const [heading, setHeading] = useState(DEFAULT_HEADING)
  const [body, setBody] = useState(DEFAULT_BODY)
  const [image, setImage] = useState('')
  const [imageFile, setImageFile] = useState<File | null>(null)
  const [isDragging, setIsDragging] = useState(false)
  const [imageError, setImageError] = useState('')
  const [saveState, setSaveState] = useState<'idle' | 'loading' | 'saving' | 'saved' | 'error'>('loading')
  const [statusMessage, setStatusMessage] = useState('Loading published content…')
  const fileInput = useRef<HTMLInputElement>(null)

  useEffect(() => {
    document.title = isEditor ? 'Edit ANON' : 'ANON'
    let active = true
    fetch('/api/anon/content', { cache: 'no-store' })
      .then(async (response) => {
        if (!response.ok) throw new Error('Could not load the published page.')
        return response.json() as Promise<{ heading: string; body: string; imageUrl?: string; updatedAt?: string }>
      })
      .then((content) => {
        if (!active) return
        setHeading(content.heading)
        setBody(content.body)
        setImage(content.imageUrl ? `${content.imageUrl}?v=${encodeURIComponent(content.updatedAt ?? '')}` : '')
        setSaveState('idle')
        setStatusMessage('Ready to edit.')
      })
      .catch((error: Error) => {
        if (!active) return
        setSaveState('error')
        setStatusMessage(error.message)
      })
    return () => { active = false }
  }, [isEditor])

  const loadImage = (file?: File) => {
    if (!file) return
    if (!file.type.startsWith('image/')) {
      setImageError('That is not an image. Try again.')
      return
    }

    const reader = new FileReader()
    reader.onload = () => {
      const result = String(reader.result)
      setImage(result)
      setImageFile(file)
      setImageError('')
    }
    reader.readAsDataURL(file)
  }

  const saveContent = async () => {
    if (!heading.trim() || !body.trim()) {
      setSaveState('error')
      setStatusMessage('Add both a heading and body paragraph before publishing.')
      return
    }
    setSaveState('saving')
    setStatusMessage('Publishing…')
    const form = new FormData()
    form.set('heading', heading)
    form.set('body', body)
    if (imageFile) form.set('image', imageFile)
    try {
      const response = await fetch('/api/anon/content', { method: 'PUT', body: form })
      const result = await response.json()
      if (!response.ok) throw new Error(result.detail || 'Could not publish the page.')
      setImageFile(null)
      setImage(result.imageUrl ? `${result.imageUrl}?v=${encodeURIComponent(result.updatedAt ?? '')}` : '')
      setSaveState('saved')
      setStatusMessage('Published. Every browser will now load this version.')
    } catch (error) {
      setSaveState('error')
      setStatusMessage(error instanceof Error ? error.message : 'Could not publish the page.')
    }
  }

  const handleFile = (event: ChangeEvent<HTMLInputElement>) => loadImage(event.target.files?.[0])

  const handleDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault()
    setIsDragging(false)
    loadImage(event.dataTransfer.files?.[0])
  }

  const handlePaste = (event: ClipboardEvent<HTMLDivElement>) => {
    const pastedImage = Array.from(event.clipboardData.items)
      .find((item) => item.type.startsWith('image/'))
      ?.getAsFile()
    if (pastedImage) {
      event.preventDefault()
      loadImage(pastedImage)
    }
  }

  return (
    <main className="anon-page">
      <section className="anon-card" aria-labelledby="anon-heading">
        <div
          className={`anon-image ${isEditor ? 'is-editable' : ''} ${isDragging ? 'is-dragging' : ''}`}
          onClick={() => isEditor && fileInput.current?.click()}
          onDragEnter={() => isEditor && setIsDragging(true)}
          onDragLeave={() => isEditor && setIsDragging(false)}
          onDragOver={(event) => isEditor && event.preventDefault()}
          onDrop={(event) => isEditor && handleDrop(event)}
          onPaste={(event) => isEditor && handlePaste(event)}
          role={isEditor ? 'button' : undefined}
          tabIndex={isEditor ? 0 : undefined}
          onKeyDown={(event) => {
            if (isEditor && (event.key === 'Enter' || event.key === ' ')) fileInput.current?.click()
          }}
          aria-label={isEditor ? 'Choose, drop, or paste an image' : undefined}
        >
          {image ? (
            <img src={image} alt="Uploaded feature" />
          ) : (
            <div className={`anon-image-prompt ${isEditor ? '' : 'is-empty'}`}>
              <strong>{isEditor ? 'DROP AN IMAGE HERE' : 'ANON'}</strong>
              {isEditor && <span>or click this slab / paste with Ctrl+V</span>}
            </div>
          )}
          <input ref={fileInput} type="file" accept="image/*" onChange={handleFile} hidden />
        </div>

        <div className="anon-copy">
          {isEditor && (
            <>
              <label htmlFor="anon-heading-input">Heading</label>
              <input
                id="anon-heading-input"
                value={heading}
                onChange={(event) => setHeading(event.target.value)}
                maxLength={80}
                spellCheck
              />

              <label htmlFor="anon-body-input">Body text</label>
              <textarea
                id="anon-body-input"
                value={body}
                onChange={(event) => setBody(event.target.value)}
                rows={5}
                maxLength={600}
                spellCheck
              />
            </>
          )}

          <div className="anon-preview">
            {isEditor && <span>LIVE COPY</span>}
            <h1 id="anon-heading">{heading || 'YOUR HEADING'}</h1>
            <p>{body || 'Your paragraph goes here.'}</p>
          </div>
          {isEditor && (
            <>
              {imageError && <p className="anon-error" role="alert">{imageError}</p>}
              <button
                className="anon-publish"
                type="button"
                onClick={saveContent}
                disabled={saveState === 'saving' || saveState === 'loading'}
              >
                {saveState === 'saving' ? 'PUBLISHING…' : 'PUBLISH THIS PAGE'}
              </button>
              <p className={`anon-note ${saveState === 'error' ? 'is-error' : ''}`} role="status">
                {statusMessage}
              </p>
            </>
          )}
        </div>
      </section>
    </main>
  )
}

export default AnonPage
