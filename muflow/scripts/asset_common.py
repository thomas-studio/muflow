# -*- coding: utf-8 -*-
"""剧本转分镜资产表 · 共用定义与生图提示词拼装。

被 build_assets_xlsx.py / migrate_add_prompts.py / validate_assets.py /
to_asset_task.py / make_prompt_list.py 共用。

v2 版式（2026-10-16 用户确认）：
    · 角色＝最左侧 1 张大型头肩肖像 ＋ 正／侧(朝右90度)／背 三个全身，同一张连续纯白画布；
    · 画幅：角色/道具/特殊资产/群像/一次性人物＝16:9 固定；场景＝16:9 或 21:9（出图前必须问用户）；
    · 全剧统一两条串：风格前缀「{媒介}画面，」＋ 统一画质串，逐字相同；
    · 角度词只允许出现在开头那一次固定顺序里（道具除外，道具必须写三角度）；
    · 栏位结构词（三栏/四栏/独立区域/竖直间隔/严格划分/栏位边界）在最终提示词里一律禁用。
"""
from __future__ import annotations

import re

# ---------------------------------------------------------------- 固定表结构

SHEETS: list[tuple[str, list[str]]] = [
    ("表1 核心角色卡",
     ["编号", "姓名", "图片", "定位", "年龄", "身材", "发型", "面部特征",
      "上装", "下装/鞋", "穿戴配饰", "气质姿态", "神态基线", "生图提示词"]),
    ("表2 核心场景卡",
     ["编号", "场景", "图片", "重要度", "空间/尺寸", "建材色调", "主要物件",
      "光线版本", "远景", "生图提示词"]),
    ("表3 分集索引表",
     ["集", "场", "场景", "场景标注", "出场角色", "角色标注", "关键动作"]),
    ("表4 资产溯源-场景",
     ["资产编号", "场景名", "类型", "首次出现", "后续复用集数", "总复用次数",
      "需要的变体版本", "备注"]),
    ("表5 资产溯源-角色",
     ["资产编号", "角色名", "类型", "首次出现", "后续复用集数", "总复用次数",
      "需要的服化变体", "备注"]),
    ("表6 一次性人物清单",
     ["编号", "人物", "出现集次", "人数", "简描(给AI出图参考)", "备注"]),
    ("出图风险提醒",
     ["编号", "风险点", "涉及资产", "具体风险", "规避措施", "优先级"]),
]

# 表7 为**可选**扩展（道具与特殊资产），位置固定在「表6」与「出图风险提醒」之间。
# 只有清单 JSON 里给了「表7 道具与特殊资产」才会生成；不生成时仍是原本的 7 张表，完全兼容。
OPTIONAL_SHEET_PROPS = (
    "表7 道具与特殊资产",
    ["编号", "资产名", "图片", "类型", "所属场景", "首次出现", "后续复用集数",
     "总复用次数", "版本", "资产描述", "生图提示词"],
)
"""「资产描述」承载道具/特殊资产的结构化描述（用途、尺寸关系、形状、比例、颜色、材质、
结构、可动部件、状态；特殊资产另加尺度、整体轮廓、结构分区、识别特征、关节或可动结构）。
一行写完，用「；」分隔；这些字段决定了能不能出图，不要留空。"""

SHEET_NAMES = [n for n, _ in SHEETS]
HEADER_ROW = 3          # 第3行是表头（1-based），数据从第4行起
DATA_START = 4
NEW_PROMPT_COLS = {"表1 核心角色卡": "生图提示词", "表2 核心场景卡": "生图提示词"}


def sheet_plan(with_props: bool = False) -> list[tuple[str, list[str]]]:
    """返回要写入的 Sheet 顺序；with_props=True 时插入表7。"""
    base = list(SHEETS)
    if with_props:
        i = next(k for k, (n, _) in enumerate(base) if n == "出图风险提醒")
        base.insert(i, OPTIONAL_SHEET_PROPS)
    return base


def sheet_order_ok(actual: list[str]) -> bool:
    """实际 Sheet 名列表是否合法（7 张，或 8 张含表7）。"""
    a = [x.strip() for x in actual]
    if a == SHEET_NAMES:
        return True
    if a == [n for n, _ in sheet_plan(True)]:
        return True
    return False


# ---------------------------------------------------------------- 全剧统一风格（项目级锁定）

DEFAULT_MEDIUM = "真人"
"""画面媒介，只能取：真人 / 二维动画 / 三维动画 / 定格动画 / 水墨动画 / 黏土动画。
不得把平台、题材、剧情类型写进这里。"""

DEFAULT_QUALITY = "成熟写实人像摄影质感，皮肤纹理与发丝细节真实，中焦段自然透视，色彩还原自然"
"""统一画质串：全剧所有资产逐字相同。"""

MEDIUM_CHOICES = ("真人", "二维动画", "三维动画", "定格动画", "水墨动画", "黏土动画")

# 兼容旧调用
DEFAULT_STYLE = DEFAULT_MEDIUM


def style_head(medium: str = "", quality: str = "") -> str:
    """拼出统一风格头：`{媒介}画面，{统一画质串}，`"""
    m = (medium or DEFAULT_MEDIUM).strip().rstrip("，,。").strip()
    if m.endswith("画面"):
        m = m[:-2]
    head = f"{m}画面，"
    q = (DEFAULT_QUALITY if quality == "" else quality).strip(" ；;，,。")
    return head if not q else f"{head}{q}，"


# ---------------------------------------------------------------- 画幅

RATIO_FIXED = "16:9"
"""角色／道具／特殊资产／群像／一次性人物：一律 16:9，不可改。"""

RATIO_SCENE_DEFAULT = "16:9"
RATIO_SCENE_OPTIONS = ("16:9", "21:9")
SCENE_RATIO_NOTICE = ("场景画幅默认 16:9、可选 21:9；**出图前必须先与用户确认本剧场景用哪个比例**，"
                      "确认前不得出场景图。")


# ---------------------------------------------------------------- 禁用词与泄漏检测

BANNED_LAYOUT_WORDS = ("三栏", "四栏", "独立区域", "竖直间隔", "严格划分", "栏位边界")
"""栏位结构词：会被模型理解成真实分隔线，最终提示词里一律不得出现。"""

ANGLE_WORDS = ("正面", "正脸", "侧面", "正侧", "背面", "背身", "侧身", "侧脸")
"""角度词：人物提示词只允许在开头固定顺序里出现一次；道具不受限。"""


def banned_layout_hits(text: str) -> list[str]:
    return [w for w in BANNED_LAYOUT_WORDS if w in (text or "")]


def angle_leak(text: str) -> list[str]:
    """正文段里残留的角度词（用于人物提示词的后半段自检）。"""
    return [w for w in ANGLE_WORDS if w in (text or "")]


# ---------------------------------------------------------------- 文本清洗

_MARK_RES = [
    re.compile(r"[（(]\s*视觉设计建议[^）)]*[）)]"),
    re.compile(r"[（(]\s*剧本未明示\s*[）)]"),
    re.compile(r"[（(]\s*视觉设计建议待确认\s*[）)]"),
    re.compile(r"[（(]\s*待确认\s*[）)]"),
]


def strip_marks(s) -> str:
    """剥掉「视觉设计建议/剧本未明示」这类标记，并把装饰性括号标签去掉。

    表里保留标记，提示词里不允许出现。
    """
    if s is None:
        return ""
    t = str(s)
    for r in _MARK_RES:
        t = r.sub("", t)
    # 图片栏状态标记 【AI出图｜…】【图生图｜…】
    t = re.sub(r"【[^】]*】", "", t)
    t = re.sub(r"\s+", " ", t)
    return t.strip(" ；;，,。")


def _join(parts) -> str:
    """用「；」拼接非空字段。值为「无」的字段直接丢弃——否则提示词里会冒出「；无，」。"""
    out = []
    for p in (strip_marks(x) for x in parts):
        if not p or p == "无":
            continue
        out.append(p)
    return "；".join(out)


# ---------------------------------------------------------------- 版式约束句（逐字复用，不得改写）

WHITE_BG = ("整张画面使用同一片均匀、连续、无缝的纯白色背景，背景固定为RGB(255,255,255)，"
            "不出现灰底、渐变、纹理、环境、地面、墙地交界或背景投影，"
            "各处亮度和颜色完全一致，使用柔和均匀的影棚光线，人物轮廓清晰")

LAYOUT_FACE_ORDER = ("同一角色在画面中出现四次，从左到右自然排列："
                     "最左侧为大型正面头肩肖像，从头顶完整显示至胸口，占画面宽度约28%；"
                     "左中为正面全身站姿；右中为朝画面右侧的唯一90度正侧面全身站姿；"
                     "最右侧为正背面全身站姿")
LAYOUT_FACE_SCALE = ("后三个全身视图共同占画面宽度约72%，从头到脚完整可见，"
                     "三个全身彼此高度一致、脚底对齐、间距均匀、互不遮挡")
"""注意：『高度一致』只约束后三个全身，绝不约束头肩肖像——要求肖像与全身等高会把肖像拉成全身。"""
LAYOUT_FACE_SAME = ("四个人像为同一角色、同一张脸、同一发型、同一体型、同一套服装、"
                    "同一配色与一致比例，背部发型、服装与身体结构清晰可见")
LAYOUT_FACE_TAIL = ("画面中只有这四个人像，不出现第五个人像、不出现重复人像；"
                    "三个全身视图的头部与脚部均不裁切；无文字、无标签、无标志、无水印")

NO_STATE = "无伤口、无血迹、无绷带、无湿衣，无哭喊、无暴怒、无惊恐等戏剧表情"


def no_item_clause(wear: str = "") -> str:
    """无物品约束句。穿戴配饰里写了工牌时，自动去掉「无挂绳工牌」以免自相矛盾。"""
    base = "无任何手持、怀抱、背挎物品，无道具、无包袋、无工具、无笔本、无手机、无水杯"
    if "工牌" not in (wear or ""):
        base += "、无挂绳工牌"
    return base


NO_ITEM = no_item_clause()

SCENE_NOBODY = ("空场景，完全无人、无动物，无人物局部"
                "（无背影、无剪影、无脸、无手、无脚、无群众）；"
                "机位能看清空间结构与出入口动线，前景中景远景层次清楚；"
                "无叠加文字、无标志、无水印、无品牌与机构标识")

PROP_TAIL = ("画面中只呈现这同一件道具：正面、侧面、背面三个角度，并补一个必要细节特写；"
             "不出现第二件同类道具、不出现无关道具、不出现人物；"
             "整张画面从左边缘到右边缘始终贯穿同一片均匀、连续、无缝的纯白色背景，"
             "背景固定为RGB(255,255,255)，各处亮度和颜色完全一致；"
             "道具轮廓清晰，材质和结构完整，画面保持纯净；无文字、无标志、无水印")

SPECIAL_TAIL = ("展示必要视图，中性纯净背景；不出现无关人物；无文字、无标志、无水印")

GROUP_TAIL = ("人物横向自然排列成一行、互不遮挡、间距均匀，外观与年龄自然差异；"
              "不做单人设定图、不重复出现同一张脸、不混入主角色的脸；"
              "干净中性背景，均匀自然布光；无文字、无标志、无水印")


# ---------------------------------------------------------------- 提示词拼装

_ROLE_FIELDS = ("定位", "年龄", "身材", "发型", "面部特征")


def role_body(name: str, d: dict) -> str:
    """角色正文字段段（不含角度词，不含布局句）。"""
    return _join(
        [strip_marks(name)]
        + [f"定位：{d.get('定位','')}" if d.get("定位") else ""]
        + [d.get(k, "") for k in _ROLE_FIELDS[1:]]
        + [f"上装：{d.get('上装','')}" if d.get("上装") else "",
           f"下装与鞋：{d.get('下装/鞋','')}" if d.get("下装/鞋") else "",
           d.get("穿戴配饰", ""),
           d.get("气质姿态", ""),
           d.get("神态基线", "")]
    )


def assemble_role_prompt(name: str, d: dict, medium: str = "", quality: str = "") -> str:
    """角色母图：最左头肩肖像＋正/侧/背三全身，同一张连续纯白画布，16:9。"""
    body = role_body(name, d)
    wear = d.get("穿戴配饰", "")
    return (f"{style_head(medium, quality)}单张横向{RATIO_FIXED}角色设定图。"
            f"{LAYOUT_FACE_ORDER}。{LAYOUT_FACE_SCALE}。"
            f"{body}。"
            f"{LAYOUT_FACE_SAME}。"
            f"{WHITE_BG}。"
            f"{LAYOUT_FACE_TAIL}。"
            f"{no_item_clause(wear)}。{NO_STATE}。")


def is_stage_version(d: dict) -> bool:
    """阶段版判定：图片栏写图生图/绑定，或面部特征写了绑定母图。"""
    mark = f"{d.get('图片','')} {d.get('面部特征','')}"
    return ("图生图" in mark) or ("绑定" in mark and "母图" in mark)


def assemble_role_edit_prompt(name: str, d: dict, medium: str = "", quality: str = "") -> str:
    """阶段版：保脸换装句式，必须配母图参考图。绝不描述脸型五官肤色发型身材。

    阶段版不自动出图（只出第一状态），本句式供人工后续生成时使用。
    """
    fc = strip_marks(d.get("面部特征", ""))
    m = re.search(r"只调整(.+)$", fc)
    qi = (m.group(1).strip(" ；;。")) if m else ""
    if not qi:
        qi = "本阶段气色与身份状态按该阶段设定"
    wear = _join([
        f"上装：{d.get('上装','')}" if d.get("上装") else "",
        f"下装与鞋：{d.get('下装/鞋','')}" if d.get("下装/鞋") else "",
        d.get("穿戴配饰", ""),
    ]) or "本阶段服装"
    return (f"{style_head(medium, quality)}单张横向{RATIO_FIXED}角色设定图，"
            f"保持参考图中人物的脸型、五官、肤色、发型、身材与比例完全不变，"
            f"仅把这四个人像的服装统一改成{wear}，{qi}。"
            f"人物数量、大小、位置与排列顺序保持不变：最左侧大型正面头肩肖像、左中正面全身、"
            f"右中朝画面右侧的唯一90度正侧面全身、最右侧正背面全身。"
            f"{WHITE_BG}。{no_item_clause(d.get('穿戴配饰',''))}。")


def assemble_scene_prompt(name: str, d: dict, medium: str = "", quality: str = "",
                          ratio: str = "") -> str:
    """场景图：单一空间、完全无人无动物；场景不受「无物品」约束。

    ratio 必须是 RATIO_SCENE_OPTIONS 之一，且**必须先经用户确认**（见 SCENE_RATIO_NOTICE）。
    """
    r = (ratio or RATIO_SCENE_DEFAULT).strip()
    if r not in RATIO_SCENE_OPTIONS:
        r = RATIO_SCENE_DEFAULT
    body = _join([
        d.get("空间/尺寸", ""),
        d.get("建材色调", ""),
        f"主要物件：{d.get('主要物件','')}" if d.get("主要物件") else "",
        d.get("光线版本", ""),
        f"远景：{d.get('远景','')}" if d.get("远景") else "",
    ])
    return (f"{style_head(medium, quality)}单张横向{r}场景空间图，{strip_marks(name)}；"
            f"{body}；{SCENE_NOBODY}。")


def assemble_prop_prompt(name: str, d: dict, medium: str = "", quality: str = "") -> str:
    """道具图：必须正面/侧面/背面＋必要细节，且只呈现同一件。"""
    body = _join([
        strip_marks(name),
        d.get("资产描述", ""),
        f"用途：{d.get('用途','')}" if d.get("用途") else "",
        d.get("尺寸关系", ""), d.get("形状", ""), d.get("比例", ""), d.get("颜色", ""),
        d.get("材质", ""), d.get("结构", ""), d.get("可动部件", ""), d.get("状态", ""),
    ])
    return (f"{style_head(medium, quality)}单张横向{RATIO_FIXED}道具设定图，{body}。{PROP_TAIL}。")


def assemble_special_prompt(name: str, d: dict, medium: str = "", quality: str = "") -> str:
    """特殊资产图：载具/生物/怪物/武器/机械。"""
    kind = strip_marks(d.get("类型", "")) or "特殊资产"
    body = _join([
        strip_marks(name),
        d.get("资产描述", ""),
        f"尺度：{d.get('尺度','')}" if d.get("尺度") else "",
        d.get("整体轮廓", ""), f"结构分区：{d.get('结构分区','')}" if d.get("结构分区") else "",
        d.get("比例", ""), d.get("颜色", ""), d.get("材质", ""),
        f"识别特征：{d.get('识别特征','')}" if d.get("识别特征") else "",
        f"关节或可动结构：{d.get('关节或可动结构','')}" if d.get("关节或可动结构") else "",
    ])
    return (f"{style_head(medium, quality)}单张横向{RATIO_FIXED}{kind}设定图，{body}。{SPECIAL_TAIL}。")


def group_count(cnt: str) -> str:
    m = re.match(r"\s*(\d+)", str(cnt or ""))
    return m.group(1) if m else ""


def assemble_group_prompt(name: str, d: dict, medium: str = "", quality: str = "") -> str:
    """群像：单独横向一字排开，不做单人设定图，人数用区间下限写死。"""
    n = group_count(d.get("人数", ""))
    cnt = f"画面人数为 {n} 人；" if n else ""
    desc = strip_marks(d.get("简描(给AI出图参考)") or d.get("简描") or "")
    return (f"{style_head(medium, quality)}单张横向{RATIO_FIXED}群体人物图，{strip_marks(name)}；"
            f"{cnt}{desc}；{GROUP_TAIL}。")


def assemble_extra_prompt(name: str, d: dict, medium: str = "", quality: str = "") -> str:
    """一次性单人：与角色同版式，但定位为一次性功能人物。"""
    desc = strip_marks(d.get("简描(给AI出图参考)") or d.get("简描") or "")
    body = _join([f"定位：一次性功能人物", strip_marks(name), desc])
    return (f"{style_head(medium, quality)}单张横向{RATIO_FIXED}人物设定图。"
            f"{LAYOUT_FACE_ORDER.replace('同一角色', '同一人物')}。{LAYOUT_FACE_SCALE}。"
            f"{body}。"
            f"{LAYOUT_FACE_SAME.replace('同一角色', '同一人物')}。"
            f"{WHITE_BG}。{LAYOUT_FACE_TAIL}。{no_item_clause()}。")


def stage_mother_code(d: dict) -> str:
    """从阶段版行里抽出它绑定的母图编号，如 'A1'。"""
    for src in (d.get("图片", ""), d.get("面部特征", "")):
        m = re.search(r"绑定\s*([A-Za-z]+\d+)", str(src or ""))
        if m:
            return m.group(1)
    return ""


# ---------------------------------------------------------------- 表头匹配

def norm_key(s) -> str:
    """归一化列名：去空格、统一括号、去标注后缀，用于宽容匹配。"""
    t = str(s or "").strip()
    t = t.replace("（", "(").replace("）", ")")
    t = re.sub(r"[\s\u3000]+", "", t)
    t = re.sub(r"\(.*?\)", "", t)      # 去掉「(给AI出图参考)」这类后缀
    return t


def col(header, *names) -> int:
    """先精确匹配，再归一化匹配；找不到返回 -1。"""
    hdr = [str(x or "").strip() for x in header]
    for n in names:
        if n in hdr:
            return hdr.index(n)
    norm = [norm_key(x) for x in hdr]
    for n in names:
        k = norm_key(n)
        if k in norm:
            return norm.index(k)
    return -1


def rowdict(header, row) -> dict:
    out = {}
    for i, h in enumerate(header):
        if not h:
            continue
        out[str(h).strip()] = ("" if i >= len(row) or row[i] is None else str(row[i]).strip())
    return out
