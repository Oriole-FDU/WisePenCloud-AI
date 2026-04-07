import { useState, useRef, useEffect, useCallback } from 'react'

const API_URL = '/v1/chat/completions'
const SESSIONS_URL = '/v1/sessions'
const MODELS_URL = '/v1/models/list'

const MODEL_TYPE_NAMES = {
  1: 'standard',
  2: 'advanced',
  3: 'unknown'
}

const WEICHENG_TEXT = `围城

第一章

红海早过了，船在印度洋面上开驶着，但是太阳依然不饶人地迟落早起，侵占去大部分的夜。夜仿佛纸浸了油，变成半透明体；它给太阳拥抱住了，分不出身来，也许是给太阳陶醉了，所以夕照晚霞隐褪后的夜色也带着酡红。到红海，船身入了地球的夹层，里面闷得像黑人的腋窝。船上的法国人、德国人、俄国人、意大利人，英国人、美国人，各用各的本国话诉说对热的受不了，同时船上的中国人，也在向同国的人诉说对热的受不了。这船，倚仗人的机巧，载满人的扰攘，寄满人的希望，热闹地行着，每分钟把沾污了人气的一小方水面，还给那无情、无尽、无际的大海。

照例每年夏天有一批中国留学生学成回国。这船上也有十来个人。大多数是职业尚无着落的青年，赶在暑假初回中国，可以从容找事。那些不愁没事的留学生，或者暑假中有事不能回国的，都在暑假快完的时候赶回国，这样可以趁暑假里把事办完，省得来回奔波。这两批人里，有几位是方鸿渐的同乡或同学，他们见了面，彼此问候，谈起家乡和母校的消息，都感到十分亲切。

方鸿渐留洋回来，到邮船停靠的码头，有几个人来迎接。他下船时，心里想：留学四年，回来了，不知道家里怎样。他父亲方遁翁老先生，是个旧式读书人，在乡里颇有声望。方鸿渐从小受父亲的影响，读了不少旧书，后来进了大学，又出洋留学，思想渐渐新起来，和父亲的意见也渐渐不合了。

方鸿渐在国外四年，换了三个大学，伦敦、巴黎、柏林。随便听几门功课，兴趣颇广，心得全无，生活尤其懒散。第四年春天，他看父亲来信说，家里给他订了婚，未婚妻是点金银行的经理周厚卿的女儿。方鸿渐吓了一跳，回信说，自己年纪还轻，学业未成，不愿早婚。父亲回信骂了他一顿，说婚姻大事，父母之命，媒妁之言，哪有自己做主的道理。方鸿渐无法，只好敷衍着，心里想，等回了国再说。

船到了香港，方鸿渐上岸去玩了一天。香港的繁华，使他眼花缭乱。他走进一家书店，看见一本《怎样获得博士学位》的书，心里一动，买了下来。回到船上，他翻看那本书，原来是一个爱尔兰人办的野鸡大学，只要出钱，就可以买到博士文凭。方鸿渐心想，自己留学四年，什么学位也没拿到，回去怎么交代？不如花点钱，买个博士头衔，也好向父亲和未婚妻交代。

他按着书上的地址，写信给那个爱尔兰人，寄去三十美元，不久就收到一张博士文凭，上面写着"克莱登大学哲学博士"。方鸿渐看着这张文凭，心里又得意又惭愧。得意的是，自己也算个博士了；惭愧的是，这博士是花钱买的，不是真本事挣来的。

船到了上海，方鸿渐下船，看见码头上人来人往，热闹非凡。他雇了一辆黄包车，到了家里。父亲方遁翁见儿子回来，十分高兴，问长问短。方鸿渐把博士文凭拿出来给父亲看，父亲看了，连连点头，说："好，好，我们家也出了个博士了。"

第二天，方鸿渐去看他的未婚妻周小姐。周小姐名叫周淑英，长得还算端正，只是脾气有些娇纵。方鸿渐和她见面，觉得话不投机，心里暗暗叫苦。周淑英问他在国外的生活，方鸿渐敷衍了几句，心里想，这门亲事，恐怕要出问题。

过了几天，方遁翁对儿子说："你既然回来了，也该把婚事办了。周家那边催了好几次了。"方鸿渐支吾着说："父亲，我刚回来，事情还没着落，婚事是不是可以缓一缓？"方遁翁听了，脸色一沉，说："婚姻大事，岂是儿戏？周家是殷实人家，淑英也是个好姑娘，你还有什么不满意的？"

方鸿渐不敢再说，心里却十分苦恼。他想，自己留学四年，花了家里不少钱，如今连个像样的工作都没有，还要和一个不喜欢的女子结婚，这日子怎么过？他想起在船上认识的一个姓苏的留学生，那人告诉他，回国后可以到报馆或学校找事。方鸿渐决定去试试。

他先去了一家报馆，人家说没有空缺。又去了一所大学，人家说要有经验的。方鸿渐四处碰壁，心里十分沮丧。他想，自己这个博士，原来是个摆设，中看不中用。

这天，他收到一封信，是他在船上认识的一位姓唐的小姐写来的。唐小姐叫唐晓芙，是他在船上认识的，两人谈得很投机。信里说，她已经到了上海，问方鸿渐有没有空见面。方鸿渐看了信，心里很高兴，立刻回信约了时间地点。

方鸿渐和唐晓芙见面，两人谈得很愉快。唐晓芙是个活泼开朗的姑娘，比方鸿渐想象中还要可爱。方鸿渐心想，要是能和这样的姑娘在一起，该多好。可是，他已经有了未婚妻，这可怎么办？

方鸿渐回到家，父亲问他事情找得怎样了。方鸿渐说还没着落。方遁翁说："你先别急，我已经托人给你在点金银行谋了个位置，过几天就可以去上班。"方鸿渐听了，心里一沉。点金银行是周淑英父亲开的，去那里上班，岂不是更摆脱不了这门亲事？

方鸿渐躺在床上，翻来覆去睡不着。他想，自己的人生，就像一艘船，被别人操控着方向，自己却无能为力。他想起唐晓芙的笑脸，想起周淑英的娇纵，想起父亲的说教，心里乱成一团。

窗外，月亮升起来了，照着上海的夜。方鸿渐望着月亮，叹了一口气。他想，这就是人生吧，充满了无奈和妥协。他闭上眼睛，希望明天会好一些。`

const TEXT_OPTIONS = [
  { id: 'weicheng', name: '围城', author: '钱钟书', content: WEICHENG_TEXT }
]

function App() {
  const [sessionId, setSessionId] = useState('')
  const [userId] = useState('test-user-001')
  const [fromSource] = useState('APISIX-wX0iR6tY')
  const [isCreatingSession, setIsCreatingSession] = useState(false)
  const [models, setModels] = useState([])
  const [selectedModel, setSelectedModel] = useState('')
  const [isLoadingModels, setIsLoadingModels] = useState(false)
  const [selectedText, setSelectedText] = useState('')
  const [customPrompt, setCustomPrompt] = useState('请针对以下内容回答：')
  const [sessions, setSessions] = useState([])
  const [isLoadingSessions, setIsLoadingSessions] = useState(false)
  const [showSessionPanel, setShowSessionPanel] = useState(false)
  const [newSessionTitle, setNewSessionTitle] = useState('新会话')
  const [messages, setMessages] = useState([])
  const [isLoading, setIsLoading] = useState(false)
  const [error, setError] = useState(null)
  const [connectionStatus, setConnectionStatus] = useState('disconnected')
  const messagesEndRef = useRef(null)
  const abortControllerRef = useRef(null)
  const [currentText, setCurrentText] = useState(null)
  const [showTextSelector, setShowTextSelector] = useState(false)

  useEffect(() => {
    loadModels()
    loadSessions()
  }, [])

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  useEffect(() => {
    if (sessionId) {
      loadSessionHistory(sessionId)
    } else {
      setMessages([])
    }
  }, [sessionId])

  const loadModels = async () => {
    setIsLoadingModels(true)
    try {
      const response = await fetch(MODELS_URL, {
        headers: { 'X-User-Id': userId, 'X-From-Source': fromSource },
      })
      const json = await response.json()
      const modelList = json?.data?.models || []
      const defaultModel = modelList.find(m => m.is_default)?.id || modelList[0]?.id || ''
      setModels(modelList)
      setSelectedModel(defaultModel)
    } catch (err) {
      console.error('加载模型列表失败:', err)
    } finally {
      setIsLoadingModels(false)
    }
  }

  const loadSessions = async () => {
    setIsLoadingSessions(true)
    try {
      const response = await fetch(`${SESSIONS_URL}/list?page=1&size=20`, {
        headers: { 'X-User-Id': userId, 'X-From-Source': fromSource },
      })
      const json = await response.json()
      setSessions(json?.data?.list || [])
    } catch (err) {
      console.error('加载 Session 列表失败:', err)
    } finally {
      setIsLoadingSessions(false)
    }
  }

  const createSession = async () => {
    setIsCreatingSession(true)
    try {
      const response = await fetch(`${SESSIONS_URL}/create`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-User-Id': userId, 'X-From-Source': fromSource },
        body: JSON.stringify({ title: newSessionTitle || '新会话' }),
      })
      const json = await response.json()
      if (json?.data?.id) {
        setSessionId(json.data.id)
        setMessages([])
        await loadSessions()
      }
    } catch (err) {
      console.error('创建 Session 失败:', err)
    } finally {
      setIsCreatingSession(false)
    }
  }

  const deleteSession = async (id) => {
    if (!confirm('确定要删除此会话？')) return
    try {
      const response = await fetch(`${SESSIONS_URL}/${id}`, {
        method: 'DELETE',
        headers: { 'X-User-Id': userId, 'X-From-Source': fromSource },
      })
      if (response.ok) {
        await loadSessions()
        if (sessionId === id) {
          setSessionId('')
          setMessages([])
        }
      }
    } catch (err) {
      console.error('删除 Session 失败:', err)
    }
  }

  const loadSessionHistory = async (sid) => {
    try {
      const response = await fetch(`${SESSIONS_URL}/${sid}/messages?page=1&size=50`, {
        headers: { 'X-User-Id': userId, 'X-From-Source': fromSource },
      })
      const json = await response.json()
      const historyMessages = json?.data?.items || []
      setMessages(historyMessages.map(msg => ({
        id: msg.id || `msg-${Date.now()}`,
        role: msg.role,
        content: msg.content,
        tokenCount: msg.token_count,
      })))
    } catch (err) {
      console.error('加载历史消息失败:', err)
      setMessages([])
    }
  }

  const parseSSE = (text) => {
    const lines = text.split('\n')
    const result = []
    for (const line of lines) {
      if (line.startsWith('data: ')) {
        const data = line.slice(6)
        if (data === '[DONE]') continue
        try { result.push(JSON.parse(data)) } catch (e) {}
      }
    }
    return result
  }

  const sendMessage = useCallback(async (text) => {
    if (!text.trim() || !sessionId) return

    const userMessage = { id: `user-${Date.now()}`, role: 'user', content: text }
    setMessages(prev => [...prev, userMessage])
    setIsLoading(true)
    setError(null)
    setConnectionStatus('connecting')

    const assistantMessage = { id: `assistant-${Date.now()}`, role: 'assistant', content: '', reasoning: '' }
    setMessages(prev => [...prev, assistantMessage])

    abortControllerRef.current = new AbortController()

    try {
      const response = await fetch(API_URL, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-User-Id': userId,
          'X-From-Source': fromSource,
          'Accept': 'text/event-stream',
        },
        body: JSON.stringify({ session_id: sessionId, query: text, model: selectedModel }),
        signal: abortControllerRef.current.signal,
      })

      if (!response.ok) {
        const errorText = await response.text()
        throw new Error(`HTTP ${response.status}: ${errorText}`)
      }

      setConnectionStatus('connected')
      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        const events = parseSSE(buffer)
        buffer = ''

        for (const event of events) {
          if (event.type === 'text-delta') {
            assistantMessage.content += event.textDelta || ''
            setMessages(prev => {
              const newMessages = [...prev]
              const idx = newMessages.findIndex(m => m.id === assistantMessage.id)
              if (idx !== -1) newMessages[idx] = { ...assistantMessage }
              return newMessages
            })
          } else if (event.type === 'reasoning') {
            assistantMessage.reasoning += event.reasoning || ''
            setMessages(prev => {
              const newMessages = [...prev]
              const idx = newMessages.findIndex(m => m.id === assistantMessage.id)
              if (idx !== -1) newMessages[idx] = { ...assistantMessage }
              return newMessages
            })
          }
        }
      }
    } catch (err) {
      if (err.name !== 'AbortError') {
        console.error('发送消息失败:', err)
        setError(err)
        setConnectionStatus('error')
      }
    } finally {
      setIsLoading(false)
      abortControllerRef.current = null
    }
  }, [sessionId, selectedModel, userId, fromSource])

  const handleTextSelection = () => {
    const selection = window.getSelection()
    const text = selection?.toString().trim()
    if (text) {
      setSelectedText(text)
    }
  }

  const handleSendSelection = async () => {
    if (!sessionId) { alert('请先创建会话'); return }
    if (!selectedText) { alert('请先选中文本'); return }
    await sendMessage(`${customPrompt}\n\n"${selectedText}"`)
    setSelectedText('')
  }

  const handleSelectText = (textOption) => {
    setCurrentText(textOption)
    setShowTextSelector(false)
    setMessages([])
  }

  return (
    <div style={{ height: '100vh', display: 'flex', flexDirection: 'column', background: '#f0f2f5' }}>
      {/* 顶部工具栏 */}
      <div style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        padding: '12px 20px',
        background: '#fff',
        borderBottom: '1px solid #e8e8e8',
        boxShadow: '0 1px 4px rgba(0,0,0,0.08)'
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '16px' }}>
          <h1 style={{ margin: 0, fontSize: '20px', color: '#1890ff', fontWeight: 600 }}>WisePen AI</h1>
          <button
            onClick={() => setShowTextSelector(!showTextSelector)}
            style={{
              padding: '8px 16px',
              background: currentText ? '#52c41a' : '#1890ff',
              color: '#fff',
              border: 'none',
              borderRadius: '6px',
              cursor: 'pointer',
              fontSize: '14px',
              fontWeight: 500
            }}
          >
            {currentText ? `📖 ${currentText.name}` : '➕ 添加文本'}
          </button>
          {currentText && (
            <button
              onClick={() => setCurrentText(null)}
              style={{
                padding: '8px 12px',
                background: '#ff4d4f',
                color: '#fff',
                border: 'none',
                borderRadius: '6px',
                cursor: 'pointer',
                fontSize: '14px'
              }}
            >
              关闭文本
            </button>
          )}
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
          <select
            value={selectedModel}
            onChange={(e) => setSelectedModel(e.target.value)}
            style={{ padding: '8px 12px', borderRadius: '6px', border: '1px solid #d9d9d9', fontSize: '14px' }}
          >
            {models.map(model => (
              <option key={model.id} value={model.id}>
                {model.name} {model.is_default ? '(默认)' : ''}
              </option>
            ))}
          </select>
          <span style={{
            padding: '4px 12px',
            borderRadius: '12px',
            fontSize: '12px',
            background: sessionId ? '#52c41a' : '#ff4d4f',
            color: '#fff'
          }}>
            {sessionId ? '已连接' : '未连接'}
          </span>
        </div>
      </div>

      {/* 文本选择弹窗 */}
      {showTextSelector && (
        <div style={{
          position: 'absolute',
          top: '60px',
          left: '20px',
          background: '#fff',
          borderRadius: '8px',
          boxShadow: '0 4px 12px rgba(0,0,0,0.15)',
          padding: '16px',
          zIndex: 1000,
          minWidth: '200px'
        }}>
          <div style={{ fontWeight: 600, marginBottom: '12px', color: '#333' }}>选择文本</div>
          {TEXT_OPTIONS.map(opt => (
            <div
              key={opt.id}
              onClick={() => handleSelectText(opt)}
              style={{
                padding: '12px 16px',
                cursor: 'pointer',
                borderRadius: '6px',
                background: currentText?.id === opt.id ? '#e6f7ff' : '#fafafa',
                marginBottom: '8px',
                border: currentText?.id === opt.id ? '1px solid #1890ff' : '1px solid #e8e8e8'
              }}
            >
              <div style={{ fontWeight: 500, color: '#333' }}>{opt.name}</div>
              <div style={{ fontSize: '12px', color: '#999', marginTop: '4px' }}>{opt.author}</div>
            </div>
          ))}
        </div>
      )}

      {/* 主内容区 */}
      <div style={{ flex: 1, display: 'flex', overflow: 'hidden' }}>
        {/* 左侧文本区 */}
        {currentText && (
          <div style={{
            width: '45%',
            display: 'flex',
            flexDirection: 'column',
            borderRight: '1px solid #e8e8e8',
            background: '#fff'
          }}>
            <div style={{
              padding: '16px 20px',
              borderBottom: '1px solid #e8e8e8',
              background: '#fafafa'
            }}>
              <h2 style={{ margin: 0, fontSize: '18px', color: '#333' }}>{currentText.name}</h2>
              <div style={{ fontSize: '13px', color: '#999', marginTop: '4px' }}>{currentText.author}</div>
            </div>
            <div
              onMouseUp={handleTextSelection}
              style={{
                flex: 1,
                overflow: 'auto',
                padding: '24px',
                fontSize: '15px',
                lineHeight: '1.8',
                color: '#333',
                userSelect: 'text'
              }}
            >
              {currentText.content.split('\n').map((para, idx) => (
                <p key={idx} style={{ textIndent: '2em', margin: '0 0 1em 0' }}>{para}</p>
              ))}
            </div>
            {selectedText && (
              <div style={{
                padding: '12px 20px',
                background: '#e6f7ff',
                borderTop: '1px solid #91d5ff',
                display: 'flex',
                alignItems: 'center',
                gap: '12px'
              }}>
                <span style={{ fontSize: '13px', color: '#1890ff' }}>
                  已选中 {selectedText.length} 字
                </span>
                <input
                  type="text"
                  value={customPrompt}
                  onChange={(e) => setCustomPrompt(e.target.value)}
                  style={{
                    flex: 1,
                    padding: '6px 12px',
                    border: '1px solid #d9d9d9',
                    borderRadius: '4px',
                    fontSize: '13px'
                  }}
                />
                <button
                  onClick={handleSendSelection}
                  disabled={isLoading || !sessionId}
                  style={{
                    padding: '6px 16px',
                    background: '#1890ff',
                    color: '#fff',
                    border: 'none',
                    borderRadius: '4px',
                    cursor: sessionId ? 'pointer' : 'not-allowed',
                    opacity: sessionId ? 1 : 0.5
                  }}
                >
                  发送
                </button>
                <button
                  onClick={() => setSelectedText('')}
                  style={{
                    padding: '6px 12px',
                    background: '#fff',
                    border: '1px solid #d9d9d9',
                    borderRadius: '4px',
                    cursor: 'pointer'
                  }}
                >
                  取消
                </button>
              </div>
            )}
          </div>
        )}

        {/* 右侧会话区 */}
        <div style={{
          flex: 1,
          display: 'flex',
          flexDirection: 'column',
          background: '#fff',
          minWidth: 0
        }}>
          {/* Session 选择 */}
          <div style={{
            padding: '12px 20px',
            borderBottom: '1px solid #e8e8e8',
            display: 'flex',
            alignItems: 'center',
            gap: '12px',
            background: '#fafafa'
          }}>
            <button
              onClick={createSession}
              disabled={isCreatingSession}
              style={{
                padding: '8px 16px',
                background: '#52c41a',
                color: '#fff',
                border: 'none',
                borderRadius: '6px',
                cursor: 'pointer',
                fontSize: '14px'
              }}
            >
              {isCreatingSession ? '创建中...' : '新建会话'}
            </button>
            <button
              onClick={() => setShowSessionPanel(!showSessionPanel)}
              style={{
                padding: '8px 16px',
                background: showSessionPanel ? '#1890ff' : '#fff',
                color: showSessionPanel ? '#fff' : '#333',
                border: '1px solid #d9d9d9',
                borderRadius: '6px',
                cursor: 'pointer',
                fontSize: '14px'
              }}
            >
              会话列表
            </button>
            {sessionId && (
              <span style={{ fontSize: '13px', color: '#666' }}>
                当前: {sessions.find(s => s.id === sessionId)?.title || sessionId.slice(0, 8)}
              </span>
            )}
          </div>

          {/* Session 列表 */}
          {showSessionPanel && (
            <div style={{
              padding: '12px 20px',
              background: '#f5f5f5',
              borderBottom: '1px solid #e8e8e8',
              maxHeight: '200px',
              overflow: 'auto'
            }}>
              <div style={{ display: 'flex', gap: '8px', marginBottom: '12px' }}>
                <input
                  type="text"
                  value={newSessionTitle}
                  onChange={(e) => setNewSessionTitle(e.target.value)}
                  placeholder="新会话标题"
                  style={{
                    flex: 1,
                    padding: '8px 12px',
                    border: '1px solid #d9d9d9',
                    borderRadius: '4px',
                    fontSize: '14px'
                  }}
                />
                <button onClick={createSession} disabled={isCreatingSession} style={{
                  padding: '8px 16px',
                  background: '#52c41a',
                  color: '#fff',
                  border: 'none',
                  borderRadius: '4px',
                  cursor: 'pointer'
                }}>
                  创建
                </button>
              </div>
              {sessions.length === 0 ? (
                <div style={{ textAlign: 'center', color: '#999', padding: '20px' }}>暂无会话</div>
              ) : (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                  {sessions.map(session => (
                    <div
                      key={session.id}
                      onClick={() => { setSessionId(session.id); setShowSessionPanel(false); }}
                      style={{
                        display: 'flex',
                        justifyContent: 'space-between',
                        alignItems: 'center',
                        padding: '10px 14px',
                        background: session.id === sessionId ? '#e6f7ff' : '#fff',
                        borderRadius: '6px',
                        border: session.id === sessionId ? '2px solid #1890ff' : '1px solid #e8e8e8',
                        cursor: 'pointer'
                      }}
                    >
                      <div>
                        <div style={{ fontWeight: 500 }}>{session.title || '无标题'}</div>
                        <div style={{ fontSize: '12px', color: '#999' }}>
                          {session.created_at ? new Date(session.created_at).toLocaleString() : ''}
                        </div>
                      </div>
                      <button
                        onClick={(e) => { e.stopPropagation(); deleteSession(session.id); }}
                        style={{
                          padding: '4px 10px',
                          background: '#fff1f0',
                          color: '#ff4d4f',
                          border: '1px solid #ffa39e',
                          borderRadius: '4px',
                          cursor: 'pointer',
                          fontSize: '12px'
                        }}
                      >
                        删除
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          {/* 消息列表 */}
          <div style={{
            flex: 1,
            overflow: 'auto',
            padding: '20px',
            background: '#fafafa'
          }}>
            {!sessionId && !currentText && (
              <div style={{
                textAlign: 'center',
                padding: '60px 20px',
                color: '#999'
              }}>
                <div style={{ fontSize: '48px', marginBottom: '16px' }}>💬</div>
                <div style={{ fontSize: '18px', marginBottom: '8px' }}>欢迎使用 WisePen AI</div>
                <div style={{ fontSize: '14px' }}>点击"添加文本"选择阅读材料，或"新建会话"开始对话</div>
              </div>
            )}
            {!sessionId && currentText && (
              <div style={{
                textAlign: 'center',
                padding: '40px 20px',
                color: '#999'
              }}>
                <div style={{ fontSize: '16px', marginBottom: '8px' }}>请先创建会话</div>
                <div style={{ fontSize: '14px' }}>选中左侧文本后，点击"发送"与 AI 讨论</div>
              </div>
            )}
            {messages.map((msg, idx) => (
              <div
                key={msg.id || idx}
                style={{
                  display: 'flex',
                  justifyContent: msg.role === 'user' ? 'flex-end' : 'flex-start',
                  marginBottom: '16px'
                }}
              >
                <div style={{
                  maxWidth: '75%',
                  padding: '12px 16px',
                  borderRadius: msg.role === 'user' ? '16px 16px 4px 16px' : '16px 16px 16px 4px',
                  background: msg.role === 'user' ? '#1890ff' : '#fff',
                  color: msg.role === 'user' ? '#fff' : '#333',
                  boxShadow: '0 1px 2px rgba(0,0,0,0.1)'
                }}>
                  {msg.role === 'assistant' && msg.reasoning && (
                    <div style={{
                      marginBottom: '8px',
                      padding: '8px 12px',
                      background: '#f6f6f6',
                      borderRadius: '8px',
                      fontSize: '13px',
                      color: '#666',
                      borderLeft: '3px solid #faad14'
                    }}>
                      <div style={{ fontWeight: 500, marginBottom: '4px' }}>💭 推理过程</div>
                      <div style={{ whiteSpace: 'pre-wrap' }}>{msg.reasoning}</div>
                    </div>
                  )}
                  <div style={{ whiteSpace: 'pre-wrap', lineHeight: '1.6' }}>{msg.content}</div>
                </div>
              </div>
            ))}
            {error && (
              <div style={{
                padding: '12px 16px',
                background: '#fff2f0',
                border: '1px solid #ffccc7',
                borderRadius: '8px',
                color: '#ff4d4f',
                marginBottom: '16px'
              }}>
                <strong>错误:</strong> {error.message}
              </div>
            )}
            <div ref={messagesEndRef} />
          </div>

          {/* 输入区 */}
          <div style={{
            padding: '16px 20px',
            background: '#fff',
            borderTop: '1px solid #e8e8e8'
          }}>
            <div style={{ display: 'flex', gap: '12px' }}>
              <input
                type="text"
                placeholder={sessionId ? '输入消息...' : '请先创建会话'}
                disabled={!sessionId || isLoading}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.shiftKey && sessionId && !isLoading) {
                    sendMessage(e.target.value)
                    e.target.value = ''
                  }
                }}
                style={{
                  flex: 1,
                  padding: '12px 16px',
                  border: '1px solid #d9d9d9',
                  borderRadius: '8px',
                  fontSize: '15px',
                  outline: 'none'
                }}
              />
              <button
                onClick={() => {
                  const input = document.querySelector('.chat-input-area input')
                  if (input?.value && sessionId && !isLoading) {
                    sendMessage(input.value)
                    input.value = ''
                  }
                }}
                disabled={!sessionId || isLoading}
                style={{
                  padding: '12px 24px',
                  background: '#1890ff',
                  color: '#fff',
                  border: 'none',
                  borderRadius: '8px',
                  cursor: sessionId ? 'pointer' : 'not-allowed',
                  opacity: sessionId ? 1 : 0.5,
                  fontSize: '15px',
                  fontWeight: 500
                }}
              >
                发送
              </button>
              {isLoading && (
                <button
                  onClick={() => abortControllerRef.current?.abort()}
                  style={{
                    padding: '12px 20px',
                    background: '#ff4d4f',
                    color: '#fff',
                    border: 'none',
                    borderRadius: '8px',
                    cursor: 'pointer'
                  }}
                >
                  停止
                </button>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

export default App
