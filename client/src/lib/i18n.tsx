"use client"

import {
	createContext,
	useCallback,
	useContext,
	useEffect,
	useMemo,
	useState,
} from "react"

/**
 * Minimal translation layer for the interface.
 *
 * Keys are the English strings themselves rather than invented identifiers:
 * every `t("Papers")` that has no entry falls back to "Papers", so a partially
 * translated build still runs and adding a translation is one dictionary line.
 *
 * The choice is stored per browser (localStorage) and also decides the language
 * the server answers and the extraction prompts use — `fetchFromApi` forwards
 * it as `Accept-Language`.
 */

export type Lang = "en" | "zh"

export const LANGUAGE_STORAGE_KEY = "op_language"

const zh: Record<string, string> = {
	// Navigation / shell
	Home: "首页",
	Library: "文献库",
	Projects: "项目",
	Ask: "问答",
	Discover: "发现",
	"View all papers": "查看全部文献",
	"View all chats": "查看全部对话",
	"Open Paper": "Open Paper",
	Search: "搜索",
	"Log out": "退出登录",
	Settings: "设置",
	Account: "账号",
	Profile: "个人资料",
	Theme: "主题",
	Language: "语言",

	Feedback: "反馈",
	Plans: "套餐",
	"Sign out": "退出登录",
	"What's new": "新功能",
	"Upload Paper": "上传论文",
	"Start your next study": "开始下一步研究",
	"Organize your research": "整理你的研究",
	"Discover Research": "发现论文",
	"Find relevant research from academic sources": "从学术来源查找相关研究",

	// Home / footer
	"Search papers, projects, or ask a question...": "搜索论文、项目，或直接提问…",
	"Searching...": "搜索中…",
	"Ask your knowledge base": "向知识库提问",
	"Untitled Paper": "未命名论文",
	"Recent Papers": "最近论文",
	"View library": "查看文献库",
	Manifesto: "核心理念",
	Blog: "博客",
	About: "关于",

	// Library table & filters
	"Loading papers...": "正在加载论文…",
	"Filter papers by title, authors, organizations, or tags...": "按标题、作者、机构或标签筛选论文…",
	Title: "标题",
	"Authors": "作者",
	"No papers in your library yet": "文献库还是空的",
	Filter: "筛选",
	"Filter & Sort": "筛选与排序",
	"Sort by": "排序方式",
	"Filter by": "筛选条件",
	Tags: "标签",
	Status: "状态",
	"Filter author...": "筛选作者…",
	"Filter tag...": "筛选标签…",
	"No author found.": "没有匹配的作者。",
	"No tags found.": "没有匹配的标签。",
	"No tags": "无标签",
	"Publish Date (Newest)": "发表日期（最新）",
	"Publish Date (Oldest)": "发表日期（最早）",

	// Projects empty state
	"Organize Your Research": "整理你的研究",
	"Group papers by topic, get focused AI assistance, and streamline your literature reviews.": "按主题归类论文、获得针对性的 AI 协助，并加速文献综述。",
	"Create your first project": "创建第一个项目",
	"Learn more": "了解更多",

	// Auth
	"Sign in to Open Paper": "登录 Open Paper",
	"Use the account configured on this server.": "使用本服务器上配置的账号。",
	"Sign in": "登录",
	Email: "邮箱",
	Password: "密码",
	"No account yet?": "还没有账号？",
	"Create one": "注册一个",
	"Create an account": "注册账号",
	"This account is stored on this server only.": "账号仅保存在本服务器上。",
	"Name (optional)": "姓名（可选）",
	"Password (at least 8 characters)": "密码（至少 8 个字符）",
	"Confirm password": "确认密码",
	"Create account": "创建账号",
	"Already have an account?": "已有账号？",
	"Invalid email or password.": "邮箱或密码不正确。",
	"The two passwords don't match.": "两次输入的密码不一致。",

	"Build Your Research Library": "建立你的文献库",
	"Drop a PDF anywhere or click below to get started.": "把 PDF 拖到任意位置，或点击下方按钮开始。",
	"Upload your first paper": "上传第一篇论文",
	"Highlight and take notes": "高亮与笔记",
	"Organize by topic": "按主题归类",
	"Citations": "引用",
	"Export in any format": "多种格式导出",
	"Upload Limit Reached": "已达上传上限",
	"Upgrade": "升级",
	"Usage summary": "用量概览",
	"You're getting close to your plan limits.": "用量接近上限。",

	// Projects page
	Focus: "专注",
	"Organize by topic or goal": "按主题或目标归类",
	"AI Chat": "AI 对话",
	"Context-aware assistance": "结合上下文的助手",
	Research: "研究",
	"Streamline lit reviews": "加速文献综述",
	"View plans": "查看套餐",
	"Search projects...": "搜索项目…",
	"Retrieving your projects...": "正在加载项目…",
	"Failed to fetch projects. Please try again.": "项目加载失败，请重试。",
	"No projects match your filters": "没有符合筛选条件的项目",
	"No projects match the selected filters": "没有符合所选条件的项目",
	"New Project": "新建项目",
	"Upgrade to create more": "升级以创建更多",
	"Project limit reached": "已达项目上限",
	"Approaching project limit": "接近项目上限",
	"You're getting close to your project limit.": "项目数量接近上限。",
	"You've used your available project slots. Upgrade for more, or archive existing projects.": "可用项目名额已用完。可以升级，或归档已有项目。",
	"Loading...": "加载中…",

	// Ask / chat
	"Thinking about your question...": "正在思考你的问题…",
	"Analyzing your knowledge base...": "正在分析你的知识库…",
	"Gathering citations...": "正在收集引用…",
	"Double-checking references...": "正在复核参考文献…",
	"Formulating a response...": "正在组织回答…",
	"Verifying information...": "正在核实信息…",
	"Crafting insights...": "正在提炼要点…",
	"Synthesizing findings...": "正在综合结论…",
	"Failed to fetch papers.": "论文加载失败。",
	"Failed to load conversation history.": "对话历史加载失败。",
	"Failed to start a new conversation.": "新建对话失败。",
	"An error occurred while processing your request.": "处理请求时出错。",
	"Build Your Knowledge Base": "建立你的知识库",
	Progress: "进度",
	"Ask a follow-up": "继续追问",
	"Look for a specific citation. Find a relevant paper. Collate evidence across your library.": "查找具体引用、定位相关论文、汇总库内证据。",
	Document: "文档",

	"Highlight color": "高亮颜色",
	"Next match": "下一个匹配",
	"No results": "无结果",
	"Previous match": "上一个匹配",
	"Search (Cmd+F)": "搜索（Cmd+F）",
	"Search...": "搜索…",
	"Zoom in": "放大",
	"Zoom out": "缩小",
	"New note": "新建笔记",
	Reply: "回复",
	"Reply…": "回复…",
	"There are no annotations for this paper.": "这篇论文还没有批注。",
	"Write a note…": "写一条笔记…",
	"Write a reply…": "写回复…",
	"Write your annotation...": "写下你的批注…",
	"Edit comment": "编辑评论",
	"Just now": "刚刚",
	"New annotation": "新建批注",
	"No annotation yet.": "还没有批注。",
	"Write your notes here…": "在这里写笔记…",
	Anonymous: "匿名",
	User: "用户",
	"Click to upload, or drag and drop": "点击上传，或拖拽文件到此处",
	"Import from URL": "从链接导入",
	"Find more literature": "查找更多文献",
	"Connect external services to your account.": "把外部服务连接到你的账号。",
	Integrations: "集成",
	"Manage your account details.": "管理你的账号信息。",
	Name: "姓名",
	"Your name": "你的姓名",
	Annual: "年付",
	Base: "基础版",
	"Billing Change Scheduled": "计费周期变更已安排",
	"Change Billing Cycle": "更改计费周期",
	"Choose the plan that fits your research workflow.": "选择适合你研究工作流的方案。",
	"Coming Soon": "即将推出",
	"Contact Support": "联系支持",
	"For independent researchers and academics": "面向独立研究者与学者",
	"For research teams and organizations": "面向研究团队与机构",
	"Got it": "知道了",
	"Log In": "登录",
	Monthly: "月付",
	"Payment Incomplete": "支付未完成",
	"Payment Past Due": "支付逾期",
	"Perfect for getting started with research": "适合刚开始做研究的人",
	"Please Log In": "请先登录",
	"Please complete the payment process to activate your subscription.": "请完成支付以激活订阅。",
	"Please update your payment method to continue your subscription.": "请更新支付方式以继续订阅。",
	"Questions about pricing?": "对价格有疑问？",
	"Research Plans": "研究方案",
	Researcher: "研究者",
	"Subscription Canceled": "订阅已取消",
	"Subscription Ending": "订阅即将结束",
	TBD: "待定",
	Teams: "团队版",
	"Updating...": "更新中…",
	"Upgrade to Researcher": "升级到研究者版",
	"You need to be logged in to view the pricing details.": "登录后才能查看价格详情。",
	"Enter the URL of the PDF you want to import.": "输入要导入的 PDF 链接。",
	Import: "导入",
	"Processing your papers...": "正在处理你的论文…",
	"Upload Papers": "上传论文",
	"You can click out of this modal while your papers are uploading.": "上传过程中可以关闭此窗口。",
	Create: "创建",
	"Create a new project": "新建项目",
	Description: "描述",
	"Enter a title and description for your new project.": "为新项目填写标题和描述。",
	"Project description": "项目描述",
	"Project title": "项目标题",

	"Open Access": "开放获取",
	"Searching for:": "正在检索：",
	"Feature Comparison": "功能对比",
	Current: "当前",
	Reactivate: "重新激活",
	Recommended: "推荐",
	Features: "功能",
	Basic: "基础版",
	"Select a plan": "选择方案",

	"Focus mode": "专注模式",
	"Exit focus mode": "退出专注模式",
	"Show chat": "显示对话",
	"All annotations": "全部批注",
	Audio: "音频",
	Overview: "概览",
	Read: "阅读",
	Download: "下载",
	Share: "分享",
	New: "新",
	Cite: "引用",
	Bibliography: "参考文献",
	"Cite Paper": "引用论文",
	"Creating Audio Overview": "正在生成音频概述",
	"Download PNG": "下载 PNG",
	"Audio Overview": "音频概述",

	Short: "简短",
	Medium: "中等",
	Long: "较长",
	"~2-3 min": "约 2-3 分钟",
	"~5-7 min": "约 5-7 分钟",
	"~10-15 min": "约 10-15 分钟",
	"Concise Summary": "简明摘要",
	"Key points overview": "要点概览",

	Copy: "复制",
	Copied: "已复制",
	"Citation Style": "引用格式",
	"Share Paper": "分享论文",
	"annotations and chats": "批注与对话",
	"will be visible to anyone with the link.": "任何拿到链接的人都能看到。",

	"Show inline annotations": "显示行内批注",
	"Hide inline annotations": "隐藏行内批注",
	"Critical Analysis": "批判性分析",
	"Limitations & rebuttals": "局限与反驳",
	"Research Gaps": "研究空白",
	"Future directions": "未来方向",
	"What's Novel": "创新点",
	"Key innovations": "关键创新",
	"Add specific guidance for your audio overview": "为你的音频概述补充具体要求…",
	"Limit Reached": "已达上限",
	"Bright young female (Chinese)": "明亮年轻女声（中文）",
	"Warm, gentle female (Chinese)": "温柔女声（中文）",
	"Low, mellow male (Chinese)": "低沉醇厚男声（中文）",
	"Beijing male (Chinese)": "北京男声（中文）",
	"Chengdu male (Chinese)": "成都男声（中文）",
	"Rhythmic male (English)": "节奏感强的英文本土男声",
	"Sunny American male (English)": "阳光美式男声",
	"Playful female (Japanese)": "活泼日语女声",
	"Warm female (Korean)": "温柔韩语女声",

	"Audio Overview Limit Reached": "已达音频概述上限",
	"Custom Instructions": "自定义指令",
	"Generate a spoken summary of this paper": "生成这篇论文的语音摘要",
	GitHub: "GitHub",
	Length: "时长",
	"Make this paper public to share it with others via a unique link. All of your": "把这篇论文设为公开后，任何拿到唯一链接的人都能查看你的",
	"Select an audio overview to play": "选择要播放的音频概述",
	"Try Again": "重试",
	"Upgrade for more audio overviews →": "升级以获得更多音频概述 →",
	"View all projects": "查看全部项目",
	Voice: "音色",

	"Creating...": "正在生成…",
	"Audio overview limit reached": "已达音频概述上限",
	"Create new audio overview": "新建音频概述",

	"New audio overview": "新建",
	Generate: "生成",
	"Generating...": "正在生成…",

	"Your Personal Research Assistant": "你的个人研究助理",
	"Store and organize your papers": "收藏并整理论文",
	"Group papers by topic": "按主题归类论文",
	"Ask questions about your papers": "就论文内容提问",
	Chat: "对话",

	"Zotero connected.": "Zotero 已连接。",
	"Import and manage syncing in Settings": "在设置中导入并管理同步",
	"Use Zotero? You can": "使用 Zotero？你可以",
	"connect your library": "连接你的文献库",
	"to import papers and keep them in sync.": "以导入论文并保持同步。",
	"Dismiss Zotero hint": "关闭 Zotero 提示",

	"Project Title": "项目标题",
	"Project Description (Optional)": "项目描述（可选）",
	"Creating project...": "正在创建…",
	"Failed to create project. Please try again.": "创建项目失败，请重试。",

	"Get Started with Your Project": "开始使用你的项目",
	"Add research papers to your project, then ask questions and generate insights.": "把研究论文加入项目，然后提问并生成洞察。",
	"Upload PDFs from your computer": "从本机上传 PDF",
	"Upload PDFs from your computer or URL": "从本机或网址上传 PDF",
	"Upload New Papers": "上传新论文",
	"Drag & drop or browse →": "拖拽文件或点击浏览 →",
	"Add from Library": "从文献库添加",
	"Choose from your existing papers": "从已有论文中选择",
	"Add papers": "添加论文",
	"Ask questions": "提问",
	"Generate insights": "生成洞察",
	Papers: "论文",
	"No papers yet.": "还没有论文。",
	"Add your first paper": "添加第一篇论文",
	Chats: "对话",
	"New chat": "新建对话",
	"No chats yet.": "还没有对话。",
	Artifacts: "分析产出",
	"Create new": "新建",
	"Podcast-style discussion of your papers": "以播客形式讨论你的论文",
	"Data Table": "数据表",
	"Compare findings across papers": "跨论文对比研究结论",
	Chart: "图表",
	"Turn cited findings into a visual": "把带引用的结论做成图表",
	"Add papers to your project to create artifacts.": "添加论文后即可创建分析产出。",
	"Add papers to your project to begin discussing and analyzing them.": "添加论文后即可开始讨论和分析。",
	"Your artifacts": "你的分析产出",
	"Nothing here yet.": "这里还没有内容。",
	"Nothing here yet. Artifacts you generate appear in this list.": "这里还没有内容。你生成的分析产出会显示在列表中。",
	"Artifacts you generate appear in this list.": "你生成的分析产出会显示在列表中。",
	"Generation runs in the background — keep working while artifacts build.": "生成在后台进行，你可以继续工作。",

	"Audio Overviews": "音频概述",
	"Data Tables": "数据表",
	Shared: "已共享",
	Clear: "清除",
	"Clear Filters": "清除筛选",
	"Toggle artifacts panel": "切换分析产出面板",
	Collaborators: "协作者",
	"Manage team members and their roles in this project.": "管理项目成员及其角色。",
	"View team members in this project.": "查看项目成员。",
	"Invite More": "邀请更多",
	admin: "管理员",
	editor: "编辑者",
	viewer: "查看者",

	Invite: "邀请",
	"Invite Collaborators": "邀请协作者",
	"Add collaborators to your project. They will be notified by email.": "把协作者加入你的项目，我们会通过邮件通知他们。",
	"If they do not have an Open Paper account, they will first receive an invitation to create one.": "如果对方还没有 Open Paper 账号，会先收到一封注册邀请。",
	"name@example.com": "请输入邮箱地址",
	Role: "角色",
	Viewer: "查看者",
	Editor: "编辑者",
	Viewers: "查看者",
	Editors: "编辑者",
	"can see all papers, chats, and artifacts, but cannot create new ones.": "可以查看全部论文、对话和分析产出，但不能新建。",
	"can do all that, plus add papers and create new chats and artifacts.": "拥有上述全部权限，还可以添加论文、新建对话和分析产出。",
	"Add another": "再添加一个",
	"Sending...": "发送中…",
	"Send Invites": "发送邀请",

	"Intelligent Artifacts": "智能分析产出",

	Exit: "退出项目",

	"Add Papers": "添加论文",
	"Uploads go to your library and are added to this project.": "上传的论文会存入你的文献库，并加入此项目。",
	"Choose from papers already in your library.": "从文献库中已有的论文中选择。",
	"Choose from papers already in your library": "从文献库中已有的论文中选择",
	"Bring papers into this project.": "把论文加入此项目。",
	"You've reached the maximum of": "已达上限",
	"papers. Remove papers to add more.": "篇论文。移除部分论文后才能继续添加。",
	"Browse existing papers →": "浏览已有论文 →",
	or: "或",

	Organizations: "机构",
	Added: "添加时间",
	"No papers match your search criteria.": "没有符合筛选条件的论文。",
	"Upload your first research paper to get started. All your papers will appear here for easy access and organization.": "上传第一篇论文开始使用。你的所有论文都会显示在这里，便于查找和整理。",
	"Upload Your First Paper": "上传第一篇论文",

	"Loading usage data...": "正在加载用量数据…",
	"Usage Limits": "用量限制",
	Manage: "管理",
	"Paper Uploads": "论文上传",
	Storage: "存储空间",
	"Weekly Chat Credits": "每周对话额度",
	"Weekly Audio Overviews": "每周音频概述",
	"Weekly Data Tables": "每周数据表",
	"Weekly Discover Searches": "每周发现检索",

	"Uploading papers": "正在上传论文…",
	Failed: "失败",
	pending: "等待中",
	running: "处理中",
	completed: "已完成",

	Add: "添加",
	Tag: "标签",
	Actions: "操作",
	"Make Project": "加入项目",

	"Ask a question about your papers, analyze findings, or explore new ideas...": "就你的论文提问、分析研究结果，或探索新想法…",
	"Nice! You have used your chat credits for the week. Upgrade your plan to use more.": "本周对话额度已用完，升级方案可继续使用。",

	"Ask something about this paper.": "就这篇论文提问…",

	"Analyzing the paper...": "正在分析论文…",
	"Generating audio overview...": "正在生成音频概述…",
	"Creating your audio summary...": "正在创建语音摘要…",
	"Summarizing the paper for you...": "正在为你总结论文…",
	"Crafting your audio overview...": "正在编排音频概述…",
	"Creating a sublime audio experience...": "正在打造流畅的听觉体验…",
	"Transforming text to audio...": "正在把文本转为语音…",
	"Converting paper to audio...": "正在将论文转为音频…",
	"Nice! You've used your chat credits for the week. Upgrade your plan to continue chatting.": "本周对话额度已用完，升级方案可继续对话。",

	// Common actions
	"Upload": "上传",
	"Upload paper": "上传论文",
	"New project": "新建项目",
	"Create project": "创建项目",
	"Cancel": "取消",
	"Save": "保存",
	"Delete": "删除",
	"Rename": "重命名",
	"Edit": "编辑",
	"Close": "关闭",
	"Back": "返回",
	"Next": "下一步",
	"Done": "完成",
	"Loading": "加载中",
	"Retry": "重试",
	"Dismiss": "忽略",

	// Papers
	Papers: "论文",
	"Search papers": "搜索论文",
	"Search your library": "搜索文献库",
	"No papers yet": "还没有论文",
	"Upload your first paper to get started.": "上传第一篇论文开始使用。",
	"Annotations": "批注",
	"Notes": "笔记",
	"Summary": "摘要",
	"Highlights": "要点",
	"Authors": "作者",
	"Published": "发表时间",

	// Projects
	Project: "项目",
	"No projects yet": "还没有项目",
	"Group related papers into projects.": "把相关论文归到项目里。",

	// Chat
	"Ask about this paper": "就这篇论文提问",
	"Ask anything about your papers": "向你的文献提问",
	"Send": "发送",
	"Thinking": "思考中",
	"You": "你",
}

interface I18nContextValue {
	lang: Lang
	setLang: (next: Lang) => void
	/** Translate an English source string; unknown keys pass through. */
	t: (text: string) => string
}

const I18nContext = createContext<I18nContextValue>({
	lang: "en",
	setLang: () => {},
	t: (text) => text,
})

export function I18nProvider({ children }: { children: React.ReactNode }) {
	const [lang, setLangState] = useState<Lang>("en")

	// Read after mount: localStorage is not available during SSR, and reading it
	// during render would make the server's HTML disagree with the first client
	// paint.
	useEffect(() => {
		try {
			const stored = window.localStorage.getItem(LANGUAGE_STORAGE_KEY)
			if (stored === "zh" || stored === "en") setLangState(stored)
		} catch {
			// Private mode or a quota error: keep English.
		}
	}, [])

	useEffect(() => {
		document.documentElement.lang = lang === "zh" ? "zh-CN" : "en"
	}, [lang])

	const setLang = useCallback((next: Lang) => {
		setLangState(next)
		try {
			window.localStorage.setItem(LANGUAGE_STORAGE_KEY, next)
		} catch {
			// Nothing to do; the choice just won't survive a reload.
		}
	}, [])

	const value = useMemo<I18nContextValue>(
		() => ({
			lang,
			setLang,
			t: (text: string) => (lang === "zh" ? zh[text] ?? text : text),
		}),
		[lang, setLang],
	)

	return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>
}

export function useI18n() {
	return useContext(I18nContext)
}

/** Language for outbound requests, readable outside React (see lib/api.ts). */
export function currentRequestLanguage(): string {
	try {
		return window.localStorage.getItem(LANGUAGE_STORAGE_KEY) === "zh"
			? "zh-CN"
			: "en"
	} catch {
		return "en"
	}
}
