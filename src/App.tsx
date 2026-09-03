import { useEffect, useMemo, useRef, useState } from 'react'
import './App.css'

type Agent = {
  id: string
  role: string
  focus: string
  icon: 'compass' | 'heart' | 'chart' | 'code' | 'spark' | 'shield' | 'cube' | 'target'
}

type Sticky = {
  id: string
  phase: number
  agentId: string
  kind: 'Insight' | 'Question' | 'Risk' | 'Feature' | 'Metric' | 'Decision'
  text: string
  x: number
  y: number
  size?: 'standard' | 'wide'
}

type Phase = {
  title: string
  objective: string
  facilitator: string
}

type AgentMessage = {
  phase: number
  agentId: string
  text: string
  kind?: 'contribution' | 'critique' | 'debate' | 'revision' | 'synthesis' | 'approval'
  timestamp?: string
  meta?: Record<string, unknown>
}

type AgentActivity = {
  status: 'waiting' | 'contributed' | 'challenging' | 'revising' | 'synthesizing' | 'approved'
  count: number
  latestText?: string
}

type BlackboardState = {
  assumptions: string[]
  evidence: string[]
  concepts: string[]
  objections: string[]
  decisions: string[]
  selectedConcept?: string | null
}

type WorkshopEvent = {
  type: 'phase' | 'message' | 'sticky' | 'blackboard' | 'brief' | 'done' | 'error' | 'cancelled'
  runId?: string
  traceId?: string
  phase?: number
  title?: string
  message?: AgentMessage
  sticky?: Sticky
  blackboard?: BlackboardState
  markdown?: string
  error?: string
  meta?: Record<string, unknown>
}

const agents: Agent[] = [
  {
    id: 'facilitator',
    role: 'Facilitator',
    focus: 'Keeps the process iterative and decision-ready',
    icon: 'compass',
  },
  {
    id: 'researcher',
    role: 'User researcher',
    focus: 'Context, behavior, needs, and pain points',
    icon: 'heart',
  },
  {
    id: 'framer',
    role: 'Problem framer',
    focus: 'Insights, problem statements, and How Might We questions',
    icon: 'compass',
  },
  {
    id: 'ideation',
    role: 'Ideation lead',
    focus: 'Divergent concepts and opportunity areas',
    icon: 'spark',
  },
  {
    id: 'prototype',
    role: 'Prototype designer',
    focus: 'Tangible concepts, journeys, and MVP shape',
    icon: 'cube',
  },
  {
    id: 'validation',
    role: 'Validation lead',
    focus: 'Tests, assumptions, feedback, and iteration plan',
    icon: 'chart',
  },
  {
    id: 'business',
    role: 'Business viability',
    focus: 'Adoption, differentiation, strategic fit',
    icon: 'chart',
  },
  {
    id: 'technical',
    role: 'Technical feasibility',
    focus: 'Architecture, dependencies, build risk',
    icon: 'code',
  },
  {
    id: 'critic',
    role: 'Design critic',
    focus: 'Clarity, friction, tradeoffs, edge cases',
    icon: 'target',
  },
  {
    id: 'ethics',
    role: 'Ethics and trust',
    focus: 'Consent, safety, privacy, user control',
    icon: 'shield',
  },
]

const phases: Phase[] = [
  {
    title: 'Empathize',
    objective: 'Understand users, context, behaviors, workarounds, needs, and emotions.',
    facilitator: 'Start with the people and context before deciding what to build.',
  },
  {
    title: 'Define',
    objective: 'Synthesize research into insights, a problem statement, and How Might We questions.',
    facilitator: 'Turn messy observations into a focused problem frame.',
  },
  {
    title: 'Ideate',
    objective: 'Generate many solution directions before judging or converging.',
    facilitator: 'Expand the solution space and make alternative paths visible.',
  },
  {
    title: 'Prototype',
    objective: 'Make the strongest idea tangible through a journey, sketch, MVP, or concept.',
    facilitator: 'Translate the idea into something people can react to.',
  },
  {
    title: 'Test',
    objective: 'Evaluate the prototype, capture feedback, and decide how to iterate.',
    facilitator: 'Use feedback and evidence to decide what changes next.',
  },
]

const defaultIdea =
  'A whiteboard app where AI agents act like a design thinking team to refine an idea from multiple points of view.'

function createEmptyBlackboard(): BlackboardState {
  return {
    assumptions: [],
    evidence: [],
    concepts: [],
    objections: [],
    decisions: [],
    selectedConcept: null,
  }
}

function inferSubject(idea: string) {
  const cleaned = idea.trim().replace(/\s+/g, ' ')
  return cleaned.length > 0 ? cleaned : defaultIdea
}

function buildEmergingMarkdown(
  idea: string,
  activePhase: number,
  messages: AgentMessage[],
  stickies: Sticky[],
  blackboard: BlackboardState,
  selectedConcept: string,
) {
  const subject = inferSubject(idea)
  const currentPhase = phases[activePhase]?.title ?? phases[0].title
  const decisions = stickies.filter((sticky) => sticky.kind === 'Decision')
  const critiques = messages.filter((message) => message.text.startsWith('Critique:')).slice(-5)
  const revisions = messages.filter((message) => message.text.startsWith('Revision:')).slice(-5)
  const evidence = blackboard.evidence.slice(-4)
  const assumptions = blackboard.assumptions.slice(-4)

  const formatLine = (text: string) => `- ${text}`
  const emptyLine = '- Still forming as the swarm works.'

  return `# Emerging Consensus Brief

## Idea under review
${subject}

## Current stage
${currentPhase}

## Decisions forming
${decisions.length > 0 ? decisions.map((sticky) => formatLine(sticky.text)).join('\n') : emptyLine}

## Selected concept for prototyping
${selectedConcept ? formatLine(selectedConcept) : '- Not selected yet. Choose an Ideate Feature sticky before Prototype, or the first viable concept will be used as a fallback.'}

## Observed evidence
${evidence.length > 0 ? evidence.map(formatLine).join('\n') : '- No supplied evidence captured yet; generated claims remain working hypotheses.'}

## Working hypotheses and assumptions to validate
${assumptions.length > 0 ? assumptions.map(formatLine).join('\n') : emptyLine}

## Recent critiques
${critiques.length > 0 ? critiques.map((message) => formatLine(message.text.replace(/^Critique:\s*/, ''))).join('\n') : emptyLine}

## Recent revisions
${revisions.length > 0 ? revisions.map((message) => formatLine(message.text.replace(/^Revision:\s*/, ''))).join('\n') : emptyLine}

## Status
The final consensus brief will replace this live draft after reviewer approvals and facilitator synthesis complete.
`
}

function formatTimestamp(timestamp?: string) {
  const date = timestamp ? new Date(timestamp) : new Date()
  return new Intl.DateTimeFormat(undefined, {
    hour: 'numeric',
    minute: '2-digit',
    second: '2-digit',
  }).format(date)
}

function formatStickyText(text: string) {
  return text
    .replace(/[.,;:!?]+$/g, '')
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 8)
    .join(' ')
}

function scanText(text: string | undefined, fallback: string) {
  if (!text) {
    return fallback
  }

  return text.replace(/^(Critique|Debate|Revision):\s*/i, '')
}

function downloadFile(filename: string, content: string, type: string) {
  const blob = new Blob([content], { type })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  anchor.click()
  URL.revokeObjectURL(url)
}

async function copyToClipboard(content: string) {
  await navigator.clipboard.writeText(content)
}

function formatMarkdownList(items: string[], empty = '- None captured.') {
  return items.length > 0 ? items.map((item) => `- ${item}`).join('\n') : empty
}

function debateMessagesFrom(transcript: AgentMessage[]) {
  return transcript.filter(
    (message) => message.kind === 'debate' || /^Debate:/i.test(message.text),
  )
}

function buildMarkdownExport(data: {
  idea: string
  runId: string
  traceId: string
  selectedConcept: string
  blackboard: BlackboardState
  debateMessages: AgentMessage[]
  transcript: AgentMessage[]
  stickies: Sticky[]
  finalBrief: string
}) {
  const debateLines = data.debateMessages.map((message) => {
    const responses = Array.isArray(message.meta?.responses)
      ? `\n  - Responses: ${JSON.stringify(message.meta.responses)}`
      : ''
    return `- Phase ${message.phase + 1} ${message.agentId}: ${message.text}${responses}`
  })

  return `# Design Thinking Session Export

## Run
- Idea: ${data.idea}
- Run ID: ${data.runId || 'local'}
- Trace ID: ${data.traceId || 'none'}

## Selected concept for prototyping
${data.selectedConcept || 'No user selection captured; fallback concept applies.'}

## Blackboard
### Observed evidence
${formatMarkdownList(data.blackboard.evidence, '- No supplied evidence captured.')}

### Working hypotheses and assumptions to validate
${formatMarkdownList(data.blackboard.assumptions)}

### Concepts under consideration
${formatMarkdownList(data.blackboard.concepts)}

### Objections and risks
${formatMarkdownList(data.blackboard.objections)}

### Decisions carried forward
${formatMarkdownList(data.blackboard.decisions)}

## Debate messages and responses
${debateLines.length > 0 ? debateLines.join('\n') : '- No debate messages captured.'}

## Board stickies
${formatMarkdownList(data.stickies.map((sticky) => `${sticky.kind}: ${sticky.text} (${sticky.agentId}, phase ${sticky.phase + 1})`))}

## Transcript
${formatMarkdownList(data.transcript.map((message) => `${message.agentId} [${message.kind ?? 'contribution'}]: ${message.text}`))}

## Final brief
${data.finalBrief}
`
}

function buildTranscriptExport(data: {
  idea: string
  selectedConcept: string
  blackboard: BlackboardState
  debateMessages: AgentMessage[]
  transcript: AgentMessage[]
  finalBrief: string
}) {
  return `# Workshop Transcript Export

Idea: ${data.idea}
Selected concept for prototyping: ${data.selectedConcept || 'No user selection captured; fallback concept applies.'}

Blackboard:
- Observed evidence: ${data.blackboard.evidence.join(' | ') || 'None captured'}
- Working hypotheses: ${data.blackboard.assumptions.join(' | ') || 'None captured'}
- Objections: ${data.blackboard.objections.join(' | ') || 'None captured'}
- Decisions: ${data.blackboard.decisions.join(' | ') || 'None captured'}

Debate messages and responses:
${formatMarkdownList(data.debateMessages.map((message) => `${message.agentId}: ${message.text} ${message.meta?.responses ? JSON.stringify(message.meta.responses) : ''}`))}

Transcript:
${formatMarkdownList(data.transcript.map((message) => `[Phase ${message.phase + 1}] ${message.agentId} (${message.kind ?? 'contribution'}): ${message.text}`))}

Final brief:
${data.finalBrief}
`
}

function AgentIcon({ agent }: { agent: Agent }) {
  const commonProps = {
    fill: 'none',
    stroke: 'currentColor',
    strokeLinecap: 'round' as const,
    strokeLinejoin: 'round' as const,
    strokeWidth: 2,
  }

  const icons = {
    compass: (
      <>
        <circle cx="12" cy="12" r="8" {...commonProps} />
        <path d="m14.5 9.5-2 5-3 1 2-5 3-1Z" {...commonProps} />
      </>
    ),
    heart: <path d="M12 20s-7-4.4-7-10a4 4 0 0 1 7-2 4 4 0 0 1 7 2c0 5.6-7 10-7 10Z" {...commonProps} />,
    chart: (
      <>
        <path d="M5 19V5" {...commonProps} />
        <path d="M5 19h14" {...commonProps} />
        <path d="m8 15 3-4 3 2 4-6" {...commonProps} />
      </>
    ),
    code: (
      <>
        <path d="m9 8-4 4 4 4" {...commonProps} />
        <path d="m15 8 4 4-4 4" {...commonProps} />
      </>
    ),
    spark: (
      <>
        <path d="M12 3v5" {...commonProps} />
        <path d="M12 16v5" {...commonProps} />
        <path d="m4.2 4.2 3.5 3.5" {...commonProps} />
        <path d="m16.3 16.3 3.5 3.5" {...commonProps} />
        <path d="M3 12h5" {...commonProps} />
        <path d="M16 12h5" {...commonProps} />
      </>
    ),
    shield: <path d="M12 3 19 6v5c0 4.5-2.8 7.4-7 10-4.2-2.6-7-5.5-7-10V6l7-3Z" {...commonProps} />,
    cube: (
      <>
        <path d="m12 3 7 4-7 4-7-4 7-4Z" {...commonProps} />
        <path d="M5 7v8l7 4 7-4V7" {...commonProps} />
        <path d="M12 11v8" {...commonProps} />
      </>
    ),
    target: (
      <>
        <circle cx="12" cy="12" r="8" {...commonProps} />
        <circle cx="12" cy="12" r="4" {...commonProps} />
        <circle cx="12" cy="12" r="1" fill="currentColor" stroke="none" />
      </>
    ),
  }

  return (
    <div className="avatar" data-agent={agent.id} aria-hidden="true">
      <svg viewBox="0 0 24 24">{icons[agent.icon]}</svg>
    </div>
  )
}

function App() {
  const [theme, setTheme] = useState<'light' | 'dark'>(() => {
    const savedTheme = window.localStorage.getItem('design-thinking-theme')
    return savedTheme === 'dark' ? 'dark' : 'light'
  })
  const [idea, setIdea] = useState('')
  const [hasStarted, setHasStarted] = useState(false)
  const [activePhase, setActivePhase] = useState(0)
  const [isAutoPlaying, setIsAutoPlaying] = useState(false)
  const [isStreaming, setIsStreaming] = useState(false)
  const [apiStatus, setApiStatus] = useState<'local' | 'streaming' | 'complete' | 'cancelled' | 'error'>('local')
  const [serverStickies, setServerStickies] = useState<Sticky[]>([])
  const [serverMessages, setServerMessages] = useState<AgentMessage[]>([])
  const [serverBlackboard, setServerBlackboard] = useState<BlackboardState>(() => createEmptyBlackboard())
  const [serverMarkdown, setServerMarkdown] = useState('')
  const [selectedConcept, setSelectedConcept] = useState('')
  const [apiError, setApiError] = useState('')
  const [currentRunId, setCurrentRunId] = useState('')
  const [traceId, setTraceId] = useState('')
  const [runStartedAt, setRunStartedAt] = useState<number | null>(null)
  const [elapsedSeconds, setElapsedSeconds] = useState(0)
  const eventSourceRef = useRef<EventSource | null>(null)
  const reconnectTimerRef = useRef<number | null>(null)
  const lastEventSequenceRef = useRef(0)
  const processedEventSequencesRef = useRef<Set<number>>(new Set())
  const streamTerminalRef = useRef(false)
  const transcriptRef = useRef<HTMLDivElement | null>(null)
  const useBackend = import.meta.env.PROD || import.meta.env.VITE_USE_BACKEND === 'true'

  useEffect(() => {
    document.documentElement.dataset.theme = theme
    window.localStorage.setItem('design-thinking-theme', theme)
  }, [theme])

  const visibleStickies = useMemo(
    () => serverStickies.filter((sticky) => sticky.phase <= activePhase),
    [activePhase, serverStickies],
  )
  const transcript = useMemo(
    () => serverMessages.filter((message) => message.phase <= activePhase),
    [activePhase, serverMessages],
  )
  const selectedConceptText = selectedConcept || serverBlackboard.selectedConcept || ''
  const markdown = useMemo(() => {
    if (serverMarkdown) {
      return serverMarkdown
    }

    if (!hasStarted) {
      return ''
    }

    return buildEmergingMarkdown(
      idea,
      activePhase,
      serverMessages,
      serverStickies,
      serverBlackboard,
      selectedConceptText,
    )
  }, [
    activePhase,
    hasStarted,
    idea,
    serverMarkdown,
    serverMessages,
    serverBlackboard,
    serverStickies,
    selectedConceptText,
  ])
  const hasFinalBrief = serverMarkdown.length > 0
  const ideateConceptStickies = useMemo(
    () => serverStickies.filter((sticky) => sticky.phase === 2 && sticky.kind === 'Feature'),
    [serverStickies],
  )
  const debateMessages = useMemo(() => debateMessagesFrom(transcript), [transcript])
  const sessionExport = useMemo(
    () => ({
      idea: inferSubject(idea),
      runId: currentRunId,
      traceId,
      phases,
      agents,
      selectedConcept: selectedConceptText,
      blackboard: serverBlackboard,
      debateMessages,
      stickies: visibleStickies,
      transcript,
      finalBrief: markdown,
    }),
    [
      currentRunId,
      debateMessages,
      idea,
      markdown,
      selectedConceptText,
      serverBlackboard,
      traceId,
      transcript,
      visibleStickies,
    ],
  )

  useEffect(() => {
    if (!isAutoPlaying || !hasStarted) {
      return
    }

    const timer = window.setInterval(() => {
      setActivePhase((current) => {
        if (current >= phases.length - 1) {
          setIsAutoPlaying(false)
          return current
        }
        return current + 1
      })
    }, 2200)

    return () => window.clearInterval(timer)
  }, [hasStarted, isAutoPlaying])

  useEffect(() => {
    return () => {
      eventSourceRef.current?.close()
      if (reconnectTimerRef.current) {
        window.clearTimeout(reconnectTimerRef.current)
      }
    }
  }, [])

  useEffect(() => {
    if (!runStartedAt || apiStatus !== 'streaming') {
      return
    }

    const timer = window.setInterval(() => {
      setElapsedSeconds(Math.floor((Date.now() - runStartedAt) / 1000))
    }, 1000)

    return () => window.clearInterval(timer)
  }, [apiStatus, runStartedAt])

  useEffect(() => {
    transcriptRef.current?.scrollTo({
      top: transcriptRef.current.scrollHeight,
      behavior: 'smooth',
    })
  }, [transcript.length])

  const currentPhase = phases[activePhase]
  const isComplete = apiStatus === 'complete'
  const displayedPhaseTitle = isComplete ? 'Complete' : currentPhase.title
  const displayedPhaseObjective = isComplete
    ? 'The swarm has synthesized, reviewed, and packaged the workshop handoff.'
    : currentPhase.objective
  const activePhaseMessages = transcript.filter((message) => message.phase === activePhase)
  const activeDiscussion = activePhaseMessages[activePhaseMessages.length - 1]
  const agentActivity = useMemo(() => {
    const activity = new Map<string, AgentActivity>()
    agents.forEach((agent) => activity.set(agent.id, { status: 'waiting', count: 0 }))
    activePhaseMessages.forEach((message) => {
      const status =
        message.kind === 'critique'
          ? 'challenging'
          : message.kind === 'revision' || message.kind === 'debate'
            ? 'revising'
            : message.kind === 'synthesis'
              ? 'synthesizing'
              : message.kind === 'approval'
                ? 'approved'
                : 'contributed'
      activity.set(message.agentId, {
        status,
        count: (activity.get(message.agentId)?.count ?? 0) + 1,
        latestText: message.text,
      })
    })
    return activity
  }, [activePhaseMessages])
  const latestDecisionSticky = visibleStickies.filter((sticky) => sticky.kind === 'Decision').slice(-1)[0]
  const latestDecision = serverBlackboard.decisions[serverBlackboard.decisions.length - 1] ?? latestDecisionSticky?.text
  const finalObjection = transcript
    .filter((message) => message.kind === 'approval' && /\bobject\b/i.test(message.text))
    .slice(-1)[0]
  const latestObjection = serverBlackboard.objections[serverBlackboard.objections.length - 1]
  const unresolvedObjection = finalObjection?.text ?? (hasFinalBrief ? undefined : latestObjection)
  const canStart = idea.trim().length > 0 && !isStreaming
  const canSelectConcept = hasStarted && ideateConceptStickies.length > 0 && !hasFinalBrief && apiStatus === 'streaming' && activePhase <= 2
  const phaseHasContent = (phaseIndex: number) =>
    serverMessages.some((message) => message.phase === phaseIndex) ||
    serverStickies.some((sticky) => sticky.phase === phaseIndex)

  const resetServerState = () => {
    eventSourceRef.current?.close()
    eventSourceRef.current = null
    if (reconnectTimerRef.current) {
      window.clearTimeout(reconnectTimerRef.current)
      reconnectTimerRef.current = null
    }
    lastEventSequenceRef.current = 0
    processedEventSequencesRef.current = new Set()
    streamTerminalRef.current = false
    setServerStickies([])
    setServerMessages([])
    setServerBlackboard(createEmptyBlackboard())
    setServerMarkdown('')
    setSelectedConcept('')
    setApiError('')
    setCurrentRunId('')
    setTraceId('')
    setRunStartedAt(null)
    setElapsedSeconds(0)
    setApiStatus('local')
    setIsStreaming(false)
  }

  const resetWorkshop = () => {
    resetServerState()
    setIdea('')
    setHasStarted(false)
    setActivePhase(0)
    setIsAutoPlaying(false)
  }

  const startLocalWorkshop = () => {
    resetServerState()
    setHasStarted(true)
    setActivePhase(0)
    setIsAutoPlaying(false)
  }

  const applyWorkshopEvent = (update: WorkshopEvent) => {
    const sequence = typeof update.meta?.sequence === 'number' ? update.meta.sequence : undefined
    if (sequence) {
      if (processedEventSequencesRef.current.has(sequence)) {
        return
      }
      processedEventSequencesRef.current.add(sequence)
      lastEventSequenceRef.current = Math.max(lastEventSequenceRef.current, sequence)
    }

    if (update.runId) {
      setCurrentRunId(update.runId)
    }
    if (update.traceId) {
      setTraceId(update.traceId)
    }
    if (typeof update.phase === 'number') {
      setActivePhase(update.phase)
    }
    if (update.message) {
      setServerMessages((messages) => [...messages, update.message as AgentMessage])
      const messageConcept = update.message.meta?.selectedConcept
      if (typeof messageConcept === 'string') {
        setSelectedConcept(messageConcept)
      }
    }
    if (update.sticky) {
      setServerStickies((stickies) => [...stickies, update.sticky as Sticky])
    }
    if (update.blackboard) {
      setServerBlackboard(update.blackboard)
      if (update.blackboard.selectedConcept) {
        setSelectedConcept(update.blackboard.selectedConcept)
      }
    }
    if (update.markdown) {
      setServerMarkdown(update.markdown)
    }
    if (update.error) {
      setApiError(update.error)
    }
    if (update.type === 'done') {
      streamTerminalRef.current = true
      setIsStreaming(false)
      setApiStatus('complete')
      eventSourceRef.current?.close()
    }
    if (update.type === 'cancelled') {
      streamTerminalRef.current = true
      setIsStreaming(false)
      setApiStatus('cancelled')
      eventSourceRef.current?.close()
    }
    if (update.type === 'error') {
      streamTerminalRef.current = true
      setIsStreaming(false)
      setApiStatus('error')
      eventSourceRef.current?.close()
    }
  }

  const openEventStream = (streamUrl: string, runId: string, attempt = 0) => {
    eventSourceRef.current?.close()
    const url = new URL(streamUrl, window.location.origin)
    if (lastEventSequenceRef.current > 0) {
      url.searchParams.set('since', String(lastEventSequenceRef.current))
    }
    const source = new EventSource(`${url.pathname}${url.search}`)
    eventSourceRef.current = source

    source.onmessage = (event) => {
      setApiError('')
      applyWorkshopEvent(JSON.parse(event.data) as WorkshopEvent)
    }

    source.onerror = () => {
      source.close()
      if (streamTerminalRef.current) {
        return
      }
      if (attempt < 6) {
        setApiStatus('streaming')
        setIsStreaming(true)
        setApiError('Stream interrupted. Reconnecting without clearing accumulated output.')
        const delay = Math.min(1000 * 2 ** attempt, 8000)
        reconnectTimerRef.current = window.setTimeout(() => {
          openEventStream(`/api/workshops/stream?idea=${encodeURIComponent(inferSubject(idea))}&runId=${runId}`, runId, attempt + 1)
        }, delay)
        return
      }

      setApiStatus('error')
      setIsStreaming(false)
      setApiError(
        'Stream reconnect failed. Accumulated board and transcript are preserved; if the backend restarted, in-memory run state cannot resume.',
      )
    }
  }

  const startWorkshop = async () => {
    if (!useBackend) {
      startLocalWorkshop()
      return
    }

    resetServerState()
    setHasStarted(true)
    setActivePhase(0)
    setIsAutoPlaying(false)
    setIsStreaming(true)
    setApiStatus('streaming')
    setRunStartedAt(Date.now())
    setElapsedSeconds(0)

    try {
      const response = await fetch('/api/workshops', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ idea: inferSubject(idea) }),
      })
      if (!response.ok) {
        throw new Error(`Backend returned ${response.status}`)
      }
      const run = (await response.json()) as { id: string; traceId: string; streamUrl: string }
      setCurrentRunId(run.id)
      setTraceId(run.traceId)
      openEventStream(run.streamUrl, run.id)
    } catch (error) {
      setApiError(error instanceof Error ? error.message : 'Unable to start backend workshop.')
      setApiStatus('error')
      setIsStreaming(false)
    }
  }

  const cancelWorkshop = async () => {
    eventSourceRef.current?.close()
    if (reconnectTimerRef.current) {
      window.clearTimeout(reconnectTimerRef.current)
      reconnectTimerRef.current = null
    }
    streamTerminalRef.current = true
    setIsStreaming(false)
    setApiStatus('cancelled')
    if (currentRunId) {
      try {
        await fetch(`/api/workshop-runs/${currentRunId}/cancel`, { method: 'POST' })
      } catch (error) {
        setApiError(error instanceof Error ? error.message : 'Cancel request failed.')
      }
    }
  }

  const selectConcept = async (sticky: Sticky) => {
    setSelectedConcept(sticky.text)
    setServerBlackboard((blackboard) => ({
      ...blackboard,
      selectedConcept: sticky.text,
      decisions: blackboard.decisions.includes(`User selected concept for Prototype: ${sticky.text}`)
        ? blackboard.decisions
        : [...blackboard.decisions, `User selected concept for Prototype: ${sticky.text}`],
    }))

    if (!useBackend || !currentRunId) {
      return
    }

    try {
      const response = await fetch(`/api/workshop-runs/${currentRunId}/selected-concept`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: sticky.text, stickyId: sticky.id }),
      })
      if (!response.ok) {
        throw new Error(`Selection was not accepted (${response.status}).`)
      }
      setApiError('')
    } catch (error) {
      setApiError(error instanceof Error ? error.message : 'Selection update failed.')
    }
  }

  return (
    <main className="app-shell">
      <div className="app-toolbar">
        <span>Design Thinking Council</span>
        <button
          type="button"
          className="theme-toggle"
          aria-label={`Switch to ${theme === 'light' ? 'dark' : 'light'} mode`}
          title={`Switch to ${theme === 'light' ? 'dark' : 'light'} mode`}
          onClick={() => setTheme((current) => (current === 'light' ? 'dark' : 'light'))}
        >
          <svg viewBox="0 0 24 24" aria-hidden="true">
            {theme === 'light' ? (
              <>
                <circle cx="12" cy="12" r="4" />
                <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
              </>
            ) : (
              <path d="M20 15.5A8 8 0 0 1 8.5 4 8 8 0 1 0 20 15.5Z" />
            )}
          </svg>
        </button>
      </div>
      <section className="hero-panel">
        <div>
          <p className="eyebrow">Design Thinking Council</p>
          <h1>Think together. Challenge the idea. Leave with a buildable direction.</h1>
          <p className="hero-copy">
            Bring a rough idea into a visual design-thinking room where agents explore different
            perspectives, challenge assumptions, and turn the strongest direction into a testable handoff.
          </p>
        </div>

        <div className="prompt-card">
          <label htmlFor="idea">Idea prompt</label>
          <textarea
            id="idea"
            value={idea}
            onChange={(event) => setIdea(event.target.value)}
            placeholder="Describe the product, service, feature, or customer problem you want the agent swarm to explore."
          />
          <div className="prompt-actions">
            <button
              type="button"
              disabled={!canStart}
              onClick={() => {
                void startWorkshop()
              }}
            >
              {isStreaming ? 'Workshop running' : 'Start workshop'}
            </button>
            <button
              type="button"
              className="secondary"
              disabled={!hasStarted && idea.length === 0}
              onClick={resetWorkshop}
            >
              Reset
            </button>
            {isStreaming ? (
              <button type="button" className="secondary danger-action" onClick={cancelWorkshop}>
                Cancel run
              </button>
            ) : null}
          </div>
        </div>
      </section>

      <section className="workshop-shell" aria-live="polite">
        <section className="participants-room" aria-label="Workshop participants">
          <div className="participants-room-header">
            <p className="eyebrow">Workshop room</p>
            <h2>Design-thinking agents</h2>
            <span className="room-phase-label">
              {isComplete ? 'Workshop complete' : hasStarted ? `${currentPhase.title} round` : 'Ready to collaborate'}
            </span>
          </div>
          <div className="agent-constellation" aria-label="Agent collaboration map">
            <div className="agent-hub">
              <AgentIcon agent={agents[0]} />
              <strong>Facilitator</strong>
              <span>{apiStatus === 'streaming' ? 'Connecting perspectives' : 'Shared synthesis'}</span>
            </div>
            <div className="agent-list">
              {agents.slice(1).map((agent) => {
                const activity = agentActivity.get(agent.id) ?? { status: 'waiting', count: 0 }
                return (
                  <div className={`agent agent-${activity.status}`} key={agent.id}>
                    <AgentIcon agent={agent} />
                    <div className="agent-copy">
                      <strong>{agent.role}</strong>
                      <span>{agent.focus}</span>
                      <small>
                        <i aria-hidden="true" />
                        {activity.status === 'waiting'
                          ? 'Waiting for round'
                          : `${activity.status} · ${activity.count} ${activity.count === 1 ? 'note' : 'notes'}`}
                      </small>
                    </div>
                  </div>
                )
              })}
            </div>
            <p className="constellation-caption">
              Agents contribute independently, challenge the round, then feed decisions back to the facilitator.
            </p>
          </div>
        </section>

        <div className="phase-track" aria-label="Design thinking steps">
          {phases.map((phase, index) => (
            <button
              type="button"
              key={phase.title}
              className={index <= activePhase ? 'phase is-active' : 'phase'}
              disabled={isStreaming || (index > 0 && !phaseHasContent(index))}
              onClick={() => {
                setHasStarted(true)
                setActivePhase(index)
                setIsAutoPlaying(false)
              }}
            >
              <span>{index + 1}</span>
              {phase.title}
            </button>
          ))}
        </div>

        <div className="toolbar">
          <div>
            <p className="eyebrow">Current phase</p>
            <h2>{displayedPhaseTitle}</h2>
            <p>{displayedPhaseObjective}</p>
            <p className="status-line">
              {apiStatus === 'streaming'
                ? 'Agent swarm is generating, critiquing, debating, revising, and synthesizing.'
                : apiStatus === 'complete'
                  ? 'MAF-backed swarm complete.'
                  : apiStatus === 'cancelled'
                    ? 'Run cancelled.'
                  : apiStatus === 'error'
                    ? apiError || 'Backend unavailable. Accumulated output is preserved if a run was in progress.'
                    : 'Local demo mode.'}
            </p>
          </div>
          <div className="run-state-card">
            <span>{hasStarted ? 'Autonomous run' : 'Ready'}</span>
            <strong>
              {apiStatus === 'streaming'
                ? 'Swarming'
                : apiStatus === 'complete'
                  ? 'Complete'
                  : apiStatus === 'cancelled'
                    ? 'Cancelled'
                  : 'Waiting for idea'}
            </strong>
            {hasStarted ? <small>{elapsedSeconds}s elapsed</small> : null}
            {traceId ? <small>Trace {traceId.slice(0, 8)}</small> : null}
            {apiError && apiStatus !== 'error' ? <small>{apiError}</small> : null}
          </div>
        </div>

        <div className="session-scan" aria-label="Session flow summary">
          <article className="scan-card current-phase-card">
            <span>Current phase</span>
            <strong>{displayedPhaseTitle}</strong>
            <p>{isComplete ? 'Final brief ready for review and handoff.' : currentPhase.facilitator}</p>
          </article>
          <article className="scan-card">
            <span>Active discussion</span>
            <strong>{activeDiscussion ? (agents.find((agent) => agent.id === activeDiscussion.agentId)?.role ?? 'Agent') : 'Waiting'}</strong>
            <p>{scanText(activeDiscussion?.text, hasStarted ? 'Waiting for the next contribution.' : 'Start a workshop to begin.')}</p>
          </article>
          <article className="scan-card objection-card">
            <span>Unresolved objections</span>
            <strong>{unresolvedObjection ? 'Needs attention' : 'Clear so far'}</strong>
            <p>{scanText(unresolvedObjection, 'No open objection captured.')}</p>
          </article>
          <article className="scan-card decision-card">
            <span>Selected concept</span>
            <strong>{selectedConceptText ? 'Chosen' : isComplete ? 'See final brief' : 'Awaiting choice'}</strong>
            <p>{scanText(selectedConceptText || latestDecision, isComplete ? 'The final brief records the prototype direction.' : 'Choose a direction during Ideate.')}</p>
          </article>
        </div>

        {ideateConceptStickies.length > 0 && activePhase === 2 && !hasFinalBrief ? (
          <div className="concept-picker" aria-label="Ideate concept selection">
            <div>
              <p className="eyebrow">Choose a direction</p>
              <strong>Which concept should the swarm prototype?</strong>
              <p>
                {selectedConceptText
                  ? `Selected: ${selectedConceptText}`
                  : 'Pick the direction that feels most worth testing next.'}
              </p>
            </div>
            <div className="concept-options">
              {ideateConceptStickies.map((sticky) => (
                <button
                  type="button"
                  className={selectedConceptText === sticky.text ? 'secondary is-selected-concept' : 'secondary'}
                  key={sticky.id}
                  disabled={!canSelectConcept}
                  onClick={() => {
                    void selectConcept(sticky)
                  }}
                >
                  {formatStickyText(sticky.text)}
                </button>
              ))}
            </div>
          </div>
        ) : null}

        <div className="workspace-grid">
          <section className="board" aria-label="Design thinking whiteboard">
            <div className="board-grid" />
            <div className="board-title">
              <span>Live whiteboard</span>
              <strong>{visibleStickies.length} notes</strong>
            </div>
            {phases.map((phase, index) => (
              <div className={`cluster cluster-${index}`} key={phase.title}>
                <span>{phase.title}</span>
              </div>
            ))}
            {visibleStickies.map((sticky, index) => {
              const agent = agents.find((item) => item.id === sticky.agentId) ?? agents[0]
              const isConceptCandidate = sticky.phase === 2 && sticky.kind === 'Feature'
              const isSelectedConcept = selectedConceptText === sticky.text
              return (
                <article
                  className={`sticky sticky-${sticky.kind.toLowerCase()} ${sticky.size ?? ''} ${isConceptCandidate ? 'concept-selectable' : ''} ${isSelectedConcept ? 'is-selected-concept' : ''}`}
                  key={sticky.id}
                  role={isConceptCandidate ? 'button' : undefined}
                  tabIndex={isConceptCandidate && canSelectConcept ? 0 : undefined}
                  aria-pressed={isConceptCandidate ? isSelectedConcept : undefined}
                  onClick={() => {
                    if (isConceptCandidate && canSelectConcept) {
                      void selectConcept(sticky)
                    }
                  }}
                  onKeyDown={(event) => {
                    if (isConceptCandidate && canSelectConcept && (event.key === 'Enter' || event.key === ' ')) {
                      event.preventDefault()
                      void selectConcept(sticky)
                    }
                  }}
                  style={{
                    left: `${sticky.x}%`,
                    top: `${sticky.y}%`,
                    transform: `rotate(${index % 2 === 0 ? '-1.4deg' : '1.2deg'})`,
                  }}
                >
                  <span>{sticky.kind}</span>
                  <p>{formatStickyText(sticky.text)}</p>
                  <footer>{agent.role}</footer>
                </article>
              )
            })}
            <div className="board-legend" aria-label="Whiteboard legend">
              <span>Lane = stage</span>
              <span>Accent = note type</span>
              <span>Icon = agent</span>
            </div>
          </section>

          <aside className="agent-panel">
            <div className="agent-panel-header">
              <p className="eyebrow">Agent discussion</p>
              <h2>Live transcript</h2>
            </div>

            <div className="transcript" ref={transcriptRef}>
              {hasStarted && transcript.length > 0 ? (
                transcript.map((message, index) => {
                  const agent = agents.find((item) => item.id === message.agentId) ?? agents[0]
                  const messageKind = message.kind ? ` ${message.kind}` : ''
                  return (
                    <article
                      className={`message${messageKind}`}
                      key={`${message.phase}-${message.agentId}-${index}`}
                    >
                      <div className="message-header">
                        <div className="message-author">
                          <AgentIcon agent={agent} />
                          <strong>{agent.role}</strong>
                          {message.kind ? <span className="message-badge">{message.kind}</span> : null}
                        </div>
                        <time dateTime={message.timestamp}>{formatTimestamp(message.timestamp)}</time>
                      </div>
                      <p>{message.text}</p>
                    </article>
                  )
                })
              ) : (
                <article className="message">
                  <div className="message-header">
                    <div className="message-author">
                      <AgentIcon agent={agents[0]} />
                      <strong>Facilitator</strong>
                    </div>
                    <time>{formatTimestamp()}</time>
                  </div>
                  <p>Enter an idea and start the workshop to see the agent swarm generate, critique, revise, and synthesize.</p>
                </article>
              )}
            </div>
          </aside>
        </div>
      </section>

      <section className="artifact-panel">
        <div>
          <p className="eyebrow">{hasFinalBrief ? 'Final artifact' : 'Working artifact'}</p>
          <h2>{hasFinalBrief ? 'Final consensus brief' : 'Live synthesis'}</h2>
          <p>
            {hasFinalBrief
              ? 'This final artifact separates observed evidence, problem hypotheses, selected concept, MVP boundary, assumptions to validate, and next prototype/validation.'
              : 'This provisional synthesis updates while the swarm works. The final consensus brief will replace it with a coding-agent-ready implementation handoff when the workshop completes.'}
          </p>
        </div>
        {markdown ? (
          <>
            <pre>{markdown}</pre>
            {hasFinalBrief ? (
              <div className="export-actions">
                <button
                  type="button"
                  onClick={() => downloadFile('design-thinking-session.md', buildMarkdownExport(sessionExport), 'text/markdown')}
                >
                  Export Markdown
                </button>
                <button
                  type="button"
                  className="secondary"
                  onClick={() =>
                    downloadFile(
                      'design-thinking-session.json',
                      JSON.stringify(sessionExport, null, 2),
                      'application/json',
                    )
                  }
                >
                  Export JSON
                </button>
                <button
                  type="button"
                  className="secondary"
                  onClick={() =>
                    downloadFile(
                      'design-thinking-transcript.md',
                      buildTranscriptExport(sessionExport),
                      'text/markdown',
                    )
                  }
                >
                  Export Transcript
                </button>
                <button
                  type="button"
                  className="secondary"
                  onClick={() =>
                    downloadFile(
                      'design-thinking-board.json',
                      JSON.stringify(
                        {
                          board: {
                            stickies: visibleStickies,
                            phases,
                          },
                          blackboard: serverBlackboard,
                          debateMessages,
                          selectedConcept: selectedConceptText,
                          finalBrief: markdown,
                        },
                        null,
                        2,
                      ),
                      'application/json',
                    )
                  }
                >
                  Export Board
                </button>
                <button type="button" className="secondary" onClick={() => window.print()}>
                  Print to PDF
                </button>
                <button type="button" className="secondary" onClick={() => copyToClipboard(markdown)}>
                  Copy for coding agent
                </button>
              </div>
            ) : (
              <div className="artifact-note">
                Exports unlock after the final consensus brief is generated.
              </div>
            )}
          </>
        ) : (
          <div className="empty-artifact">
            The consensus brief will appear here after the autonomous swarm completes.
          </div>
        )}
      </section>
    </main>
  )
}

export default App
