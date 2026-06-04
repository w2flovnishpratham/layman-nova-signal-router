import { MessageRow } from './MessageRow'
import { TypingDots } from '../TypingDots'
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion'
import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import type { RenderableMessage } from '../../types'

interface Props {
  messages: RenderableMessage[]
  typing: boolean
  children: ReactNode
  panelKey: string
  inlinePanel?: ReactNode
}

export function ChatLog({ messages, typing, children, panelKey, inlinePanel }: Props) {
  const scrollRef = useRef<HTMLDivElement | null>(null)
  const bottomRef = useRef<HTMLDivElement | null>(null)
  const prefersReducedMotion = useReducedMotion()
  const reduceMotion = appReducedMotion(prefersReducedMotion)
  const { visibleMessages, stagedTyping } = useMessageReveal(messages, reduceMotion)
  const hasQueuedMessages = visibleMessages.length < messages.length
  const showTyping = stagedTyping || (typing && !hasQueuedMessages)
  const inlinePanelTargetId = inlinePanel ? latestPromptMessageId(visibleMessages) : null
  const panelChildren = hasQueuedMessages || showTyping ? null : children

  useEffect(() => {
    const scrollToBottom = () => {
      bottomRef.current?.scrollIntoView({
        block: 'end',
        behavior: reduceMotion ? 'auto' : 'smooth',
      })
    }
    const frame = window.requestAnimationFrame(scrollToBottom)
    const timeout = window.setTimeout(scrollToBottom, reduceMotion ? 0 : 320)
    return () => {
      window.cancelAnimationFrame(frame)
      window.clearTimeout(timeout)
    }
  }, [visibleMessages.length, showTyping, children, inlinePanelTargetId, reduceMotion])

  const slotMotion = reduceMotion
    ? {
        initial: { opacity: 0 },
        animate: { opacity: 1 },
        exit: { opacity: 0 },
        transition: { duration: 0 },
      }
    : {
        initial: { opacity: 0, y: 10 },
        animate: { opacity: 1, y: 0 },
        exit: { opacity: 0, y: -4 },
        transition: {
          duration: 0.2,
        },
      }

  const bubbleMotion = reduceMotion
    ? {
        initial: { opacity: 0 },
        animate: { opacity: 1 },
        exit: { opacity: 0 },
        transition: { duration: 0 },
      }
    : {
        initial: { opacity: 0 },
        animate: { opacity: 1 },
        exit: { opacity: 0 },
        transition: {
          duration: 0.16,
        },
      }

  function motionClass(type: string): string {
    return `message-motion message-motion-${type.replaceAll('.', '-')}`
  }

  return (
    <div ref={scrollRef} className="chat-log" aria-live="polite">
      <AnimatePresence initial={false}>
        {visibleMessages.map((message) => (
          <motion.div key={message.id} className={motionClass(message.type)} {...slotMotion}>
            <motion.div className="message-pop" {...bubbleMotion}>
              <MessageRow message={message} inlinePanel={message.id === inlinePanelTargetId ? inlinePanel : null} />
            </motion.div>
          </motion.div>
        ))}
        {showTyping ? (
          <motion.div key="nova-typing" className="message-motion message-motion-typing" {...slotMotion}>
            <motion.div className="message-pop" {...bubbleMotion}>
              <TypingDots />
            </motion.div>
          </motion.div>
        ) : null}
        {panelChildren ? (
          <motion.div key={`nova-active-panel-${panelKey}`} className="message-motion message-motion-panel" {...slotMotion}>
            <motion.div className="message-pop" {...bubbleMotion}>
              {panelChildren}
            </motion.div>
          </motion.div>
        ) : null}
      </AnimatePresence>
      <div ref={bottomRef} className="chat-bottom-anchor" aria-hidden="true" />
    </div>
  )
}

function useMessageReveal(messages: RenderableMessage[], reduceMotion: boolean): { visibleMessages: RenderableMessage[]; stagedTyping: boolean } {
  const [visibleMessages, setVisibleMessages] = useState<RenderableMessage[]>([])
  const [stagedTyping, setStagedTyping] = useState(false)
  const [heldUserMessageId, setHeldUserMessageId] = useState<string | null>(null)

  useEffect(() => {
    const visibleStillMatches = visibleMessages.every((message, index) => messages[index]?.id === message.id)
    if (!visibleStillMatches || visibleMessages.length > messages.length) {
      const resetTimeout = window.setTimeout(() => {
        setStagedTyping(false)
        setHeldUserMessageId(null)
        setVisibleMessages([])
      }, 0)
      return () => window.clearTimeout(resetTimeout)
    }

    if (visibleMessages.length >= messages.length) return

    const nextMessage = messages[visibleMessages.length]
    const previousMessage = visibleMessages.at(-1)
    const shouldStageTyping = shouldStageNovaTyping(nextMessage, previousMessage)
    const revealDelay = reduceMotion ? 0 : delayForMessage(nextMessage, shouldStageTyping)

    const typingTimeout = window.setTimeout(() => {
      setHeldUserMessageId(null)
      setStagedTyping(shouldStageTyping)
    }, 0)

    const revealTimeout = window.setTimeout(() => {
      setStagedTyping(false)
      setVisibleMessages((current) => {
        const currentStillMatches = current.every((message, index) => messages[index]?.id === message.id)
        if (!currentStillMatches || current.length > messages.length) return []

        const message = messages[current.length]
        return message ? [...current, message] : current
      })
    }, revealDelay)

    return () => {
      window.clearTimeout(typingTimeout)
      window.clearTimeout(revealTimeout)
    }
  }, [messages, reduceMotion, visibleMessages])

  useEffect(() => {
    if (visibleMessages.length < messages.length) return

    const lastMessage = visibleMessages.at(-1)
    if (!lastMessage || lastMessage.type !== 'client.message' || heldUserMessageId === lastMessage.id) return

    const typingTimeout = window.setTimeout(() => setStagedTyping(true), 0)
    const clearTypingTimeout = window.setTimeout(() => {
      setStagedTyping(false)
      setHeldUserMessageId(lastMessage.id)
    }, reduceMotion ? 0 : 420)

    return () => {
      window.clearTimeout(typingTimeout)
      window.clearTimeout(clearTypingTimeout)
    }
  }, [heldUserMessageId, messages.length, reduceMotion, visibleMessages])

  return { visibleMessages, stagedTyping }
}

function latestPromptMessageId(messages: RenderableMessage[]): string | null {
  const prompt = [...messages].reverse().find((message) => message.type === 'bot.message' && message.data.tone === 'prompt')
  return prompt?.id ?? null
}

function delayForMessage(message: RenderableMessage, stagedTyping: boolean): number {
  if (stagedTyping) return 460
  if (message.type === 'client.message') return 40
  return 90
}

function shouldStageNovaTyping(nextMessage: RenderableMessage, previousMessage: RenderableMessage | undefined): boolean {
  if (!isNovaMessage(nextMessage)) return false
  return !previousMessage || previousMessage.type === 'client.message'
}

function isNovaMessage(message: RenderableMessage): boolean {
  return message.type !== 'client.message' && message.type !== 'client.summary' && message.type !== 'system.event'
}

function appReducedMotion(prefersReducedMotion: boolean | null): boolean {
  const appMotion = document.documentElement.dataset.motion
  return appMotion === 'reduced' || (appMotion === 'system' && Boolean(prefersReducedMotion))
}
