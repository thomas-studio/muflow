# 数据与命令 · schema 2 / V2.3.1

作品与本机配对配置分别保存。使用项目要求和已确认参数，不继承旧作品的时长、车型或改词。源稿、执行稿、请求、采用图、退回图和每次回执分版本保留。

## 主数据

- project_id、version、schema_version=2、source_path。settings含媒介/画风、asset_ratio/scene_ratio默认16:9、单独确认的video_ratio、episode_duration_mode=story_driven、group_target_seconds=30、speech_chars_per_second默认4.5、action_seconds_per_beat默认2.2、外置配对的video_model及四个声音/文字开关。不再填写统一episode_target_seconds；各集时长为计算结果，禁止再询问150秒/集。估算口径不是实测或模型能力证明；预算和价格来自外部profile。
- adaptation_review：status、evidence，实际完成短剧适配审查后登记；不以软件检测代替。
- episodes：数字id、title及起止/集尾落点；scenes：id、episode、name、time、transition、key_action。时间未明保留未明。
- dialogues：id、episode、text、source_ids、speaker、channel、emotion、evidence；修改保留source_text及approval。episode用于检查所选集是否整句遗漏；每条及各片段按原顺序完整归入同集。数字单位的实际读法可加spoken_text，只服务时长估计，不替换锁定台词。
- entities：以稳定实例ID为键，含kind=person/vehicle/object、identity_id、name；同一身份的换装/孕期/伤衣是状态，不新建同框复制人。
- assets：精确id/name/kind、mode=generate/edit/reuse、parent_id、purpose、prompt、local_path、sha256、remote_url、remote_sha256、remote_reference_evidence、review。人物加entity_id/state_key，与镜头的visual_state匹配。purpose区分identity_sheet/video_reference/detail/scene/prop。
- edit_audit记录operation=image_edit、endpoint、input_asset_id、input_sha256、input_count=1及实际输入参数证据。有母图的派生必须edit。reuse核验已存在文件后无API请求，不要求再生图。
- 场景/载具的spatial_review写实际图片的空间适配status/evidence；identity_sheet直接用于视频时，另写video_reference_review说明该接口该镜头是否适配。
- groups：id、episode、expected_seconds、shots、bindings、prompt_path、review，可加directing_notes。revision_kind=initial/service_retry/content_redo，内容重做保留supersedes。group表示一个实际逻辑API请求，不等于集或场。
- shots：id、scene_id、seconds、camera、action、asset_ids、speech、entry_state/exit_state的人读说明，以及结构化state_in/state_out、events、entry_bridge、visible_entities、camera_subjects。状态规范看continuity.md。
- 每镜timing登记action_beats或action_seconds、动作依据、有必要的pause_seconds/transition_seconds及依据、实际同时发生的overlap_seconds/overlap_evidence、额外分配秒数的allocation_basis。对白时长由speech实际可发声正文自动统计，不能另填字数冒充已回对；详见[timing.md](timing.md)。
- speech项为id、text片段、speaker、channel、emotion；程序回拼原句，编译器把每个片段写进实际请求。sound_effects各项写physical_source、purpose及必要触发说明。
- bindings为有序name/asset_id，名称不得含@、等号或换行，生成@名称=图N并与实际数组一致。可见图不能漏传；整组共有参考不等于每镜所有人出镜。
- 跨集共享组填写uses数组：episode、shot_ids、start_second、end_second；镜头唯一归集，区间长等于镜头秒数，共享请求只生成一次。
- iterations列所有已产出图/视频：id、label（自然语言，如“首次构图”“服装修订”）、kind、local_path、sha256、status、reason、费用/母版/请求ID；用户采用须user_approval_evidence。智能体建议退回和用户退回分开。

## 分阶段执行

完整顺序与命令以[分阶段确认](staged-review.md)为准：先确认设置→仅文字/表格与全剧提示词审阅→用户允许才生图→用户审图→实际绑定的全片视频预览→视频启动。不得跳过资产表，也不得提前生成图片补审阅页面。

新增字段：

- settings.asset_ratio默认16:9，scene_ratio默认16:9，video_ratio另经用户确认。原图导入保留原始比例，阶段/换装/画布派生另走实际图片编辑。
- input_assets：[asset_id、entity_id、local_path、sha256、identity_locked]；已有固定身份母图用mode=reuse，派生ID必须以该图为祖先，不重新抽脸。
- deliverables：screenplay、asset_table、asset_prompts、storyboard、shot_prompts、selfcheck六个真实交付文件路径。缺文件不算文字交付完成。
- workflow.mode=staged_user_review；project_confirmation、text_delivery、text_approval、image_approval、video_approval独立记录真实依据、时间和版本摘要。无回复、代理判断、既有余额不替代用户同意。
- workflow.production_scope默认独立原稿或已确认分集基准的全剧范围；用户明确只做部分集数时写episodes与user_evidence，同时显示原稿全量。完整预览覆盖本次真实范围，不能从已登记少量集数推断全剧完成。

文字审阅快照包含文本源、剧本/台词/场景、全部提示词和交付文件哈希；生成结果路径/上传URL和批准记录不反向污染已确认文字。图像快照锁定实际采用图哈希，视频批准锁定已交付的视频面板。内容变更回对应审阅阶段，服务原参失败重试不重复审批。

## 预览授权数据

外部profile.authorization保留execution_enabled、scopes、budget_yuan、evidence，并增加mode及project_scope。

- mode=frozen_preview：project_scope写project_id、episodes和该面板的snapshot_digest；用户授权绑定当前精确快照。
- mode=project_pre_authorized：project_scope写project_id和用户授权的episodes；evidence明确自动工作范围/预算。仅在当前阶段已获用户确认后覆盖本阶段预算内执行；仍需文字审阅、用户审图和视频启动，不重复逐组问。
- allow_content_redo只有用户明确允许预算内内容修订试片时才为true；否则服务失败重试可继续，新增改词/换图收费版本需补足对应授权。

不要让代理凭空填写授权证据；已有直接用户指示可引用，不重复确认。变更集数、范围、模型、报价或付费重做权限时检查原授权是否覆盖。仅更新同一对象有效签名链接也应刷新预览；项目预授权下无需重复批准相同资产。

## 迁移与真实局限

升级到本版须在作品副本中移除旧统一episode_target_seconds，登记story_driven、30秒及真实逐镜timing依据，重新估算并交相应新版审阅；不改原稿或已安装的外部profile。已有有效API配对可复用，若未核验30秒能力则仅补能力核对。

schema 1可作历史文本读取，生产视频执行须迁移到schema 2并实际补充审查和状态，不能只改schema_version数字。旧版离线测试仅对本机模拟地址保留兼容，不是生产绕过门槛的开关。底层调用run也需要执行门槛产生的grant，新适配器不可直接绕过。

程序只能检查登记数据之间的关系，无法知道未登记的原文事实。原文覆盖、复杂空间语义、实际图片和改编权限仍需智能体真实核对。参考图转换、场景纠错和镜组修改会影响依赖，重编受影响请求并回核后续台词，不能只改首个试片。


## V2.2 交付/确认迁移

见[标准图册](asset-delivery.md)及[审批来源](review-controls.md)。image_delivery独立记录实际图册交付；阶段批准增加user_event（来源路径/行号/原事件摘要、实际正文/时间）和delivered_at。approve命令使用--user-log与--user-line，不再以--evidence的代理字符串替代实际用户确认。已有旧审批不能仅改字段补造，需要实际交付当前面板并引用之后的真实用户消息；不重新生成既有合格图。

image_api原始结果移到99_工作区/生图原始记录，自动登记并导出03_资产图。project.assets仍指向真实原始图哈希，图册副本单独核验；图册copies与历史均可追溯。外置profile配对方式不变，不新增实际上游、模型、价格或OSS预设。


## V2.3 内容、审查与固定页面契约

- settings.asset_review_mode=manual/agent/joint。新模板null须启动确认；旧项目完全缺key暂以joint兼容，不等于已获得用户选择。模式决定所需资产视觉结论，文件/尺寸/母图/绑定技术检查及最终视频启动均保留。
- episode_baseline可写confirmed、episode_ids、source_sha256、evidence，绑定当前source_path实际文件。已有剧本也可从原稿“第N集”标题独立提取；小说章节不作集数，先完成并确认分集基准。source_coverage.coverage返回source_episodes、registered_episodes、grouped_episodes、requested_episodes及缺项、basis_status、full_complete/scope_complete。基准未证实时页面仍可建立，但不能宣称全剧已完成。
- 资产review：status、subject_digest、performed_by（agent/human）、checked_at、evidence、checks。check包含rule_id、expected、observed、evidence、result。身份阶段identity_stage、外貌appearance为基础；scene/vehicle加space；identity_sheet加video_reference。subject_digest由review_state.asset_subject对当前身份、状态、操作模式、提示词、母图/母图哈希、图片哈希等内容生成，不由代理手写任意摘要。
- 组review采用同样结构；checks另有shot_ids，必须覆盖本组全部镜头。基础story_continuity、space，有台词加speaker_channel，有音效加physical_sound。摘要由review_state.group_subject生成，绑定当前镜头、引用场景/台词与有序绑定。非空证据或一段“全部通过”不够。
- workflow.image_approval.items每项按asset_id、sha256、subject_digest绑定真实人工用户事件。approve_images可选asset_ids，workflow_review.py approve-images可加--asset-ids；先确认母图可行。agent模式只填真实审查，不生成伪造人工采用；未选择环节不适用。
- 资产图册固定03_资产图/00_资产参考图总览.html；跑前固定07_跑前预览/全剧跑前预览.html。输入盘点后可零API创建，资产表另提供实际文件入口。页面是持续更新入口，项目version、内容摘要、导出快照和批准范围分别显示。
- build_preview.build(project,out,profile_path=None,episodes=None)保持接口；out传项目07_跑前预览，允许原位刷新，旧页面及compiled_jobs、quote_snapshot、run_scope、preview_checks、timing_plan归档history下唯一时间版本。preview_resources按图片SHA复用原图/缩略资源，避免逐镜嵌入重复大图。
- 两视图共用compile_jobs.source_digest所指内容摘要。本地缺OSS也显示实际图片/分镜/缺项；preview_checks分别记录local_issues、execution_issues、execution_ready、source_coverage与更新时间等。执行门槛仍核对实际远端对象/哈希、有效引用、有序数组、费用及当前完整预览后的用户启动。

真正视频prompt使用可执行自然分镜：实名角色、与bindings相同图序、镜号/精确30秒时间线、场景时间转场、机位动作、逐字台词声道情绪、必要物理声及关键连续性。state_in/state_out/events/entry_bridge与对象路径继续在JSON核验，不能原样dump进prompt。未知状态明确待核，不猜司机、空间、伤因或日夜；不删掉孩子、座次、持物、伤衣或阶段来精简。compose_group/compose接口保留，非法时长可编译诊断，由校验阻止执行。

普通展示刷新、审查登记及同对象引用更新不改变图片内容批准；实际变更只使相应内容及依赖待复核。固定入口刷新不意味着用户已看过新版视频预览，最终启动仍绑定实际交付的完整快照和之后真实用户消息。阶段内批次与台账见[分阶段执行](staged-review.md)，不能把批次当作阶段或创建系统自动化。


本版新增init_project.py DIRECTORY SOURCE [--project-id]提取原文与模板并立即建立三入口；已有项目用refresh_progress.py PROJECT [--profile]在每批/每次图片或审查事件后同步刷新。export_assets.py SOURCE PROJECT OUT --refresh更新固定资产表目录的资产拆解表.xlsx、资产清单源.json、生图提示词清单.md、资产图_出图任务.json，旧版归档history，均为零媒体调用。启动先完成全剧无API文字，API/OSS在全部文字交付后集中询问；配置资料不代替实际生图阶段许可。

stage_queue.py只作本地5集剧本／5集分镜资产／20张图片分批台账。图片complete仅表示已出图；next末态先awaiting_review/asset_review，统一readiness满足实际内容、全量结构、技术与所选审核后才ready_preview/prevideo_preview，未取得必需人工结论不能满血。台账不产生生图或视频授权，最终完整当前预览实际交付后仍等明确人工启动。
