"""Embedding 模型封装 — BGE-M3。

BGE-M3 输出 1024 维稠密向量，~2GB 模型权重，首次使用时自动下载/加载。
"""

import logging
import os
import threading

logger = logging.getLogger(__name__)

#: 惰性加载锁（模块级，保护下面的 `EmbeddingModel.model` 与 `get_embedding_model`）。
#: BGE-M3 权重约 2GB；真实装配会在 ThreadPoolExecutor 工作线程里首次取用检索器，
#: 于是同一时刻可能有两个线程同时看到 `self._model is None` 并各自构造一份模型。
#: 实测后果有两层：一是两倍显存/内存与重复的 HF 抓取，二是**并发原生加载会偶发段错误**
#: （exit 139，没有 Python 回溯，崩在权重加载期间）。锁把首次加载收敛成一次，
#: 后到的线程等锁并在拿到锁后复查 `_model`，因此单线程语义不变、并发语义变正确。
_LOAD_LOCK = threading.Lock()


class EmbeddingModel:
    """BGE-M3 embedding 模型，本地部署，惰性加载（线程安全）。"""

    def __init__(self, model_name: str = "BAAI/bge-m3", use_fp16: bool = True):
        self._model_name = model_name
        self._use_fp16 = use_fp16
        self._model = None

    @property
    def model(self):
        # 双重检查：先在无锁快路径上判断，未加载才进锁；进锁后**必须复查**，
        # 否则等锁期间已完成的加载会被这一轮覆盖成第二次加载。
        if self._model is None:
            with _LOAD_LOCK:
                if self._model is None:
                    # 模型 cache 后离线加载，避免每次连 HuggingFace Hub 超时
                    os.environ.setdefault("HF_HUB_OFFLINE", "1")
                    logger.info("Loading BGE-M3 model: %s (fp16=%s)",
                                self._model_name, self._use_fp16)
                    from FlagEmbedding import BGEM3FlagModel
                    self._model = BGEM3FlagModel(self._model_name, use_fp16=self._use_fp16)
        return self._model

    @property
    def model_name(self) -> str:
        """模型 ID（如 BAAI/bge-m3），供 trace 记录真实 embedding model。"""
        return self._model_name

    @property
    def device(self) -> str | None:
        """真实运行设备（模型未加载返回 None；加载后取 FlagEmbedding target_devices[0]）。"""
        if self._model is None:
            return None
        tds = getattr(self._model, "target_devices", None)
        if tds:
            return str(tds[0])
        return None

    def encode(self, texts: list[str]) -> list[list[float]]:
        """将文本列表编码为 embedding 向量列表（每行 1024 维）。

        **推理也要串行**：`FlagEmbedding` 的 `encode_single_device` 每次调用都先改**共享**模型
        的状态——`self.model.float()`（CPU 上转回 fp32）、`self.model.to(device)`、`self.model.eval()`
        ——然后才做前向。检索按 `_run_channels` 起线程池（`retriever_v2`），同一进程里可以同时
        有两个检索会话，于是两个线程会**同时**改同一个模型 dtype/device 并同时前向。实测后果
        有两种形态，都出现过：轻则「expected scalar type Half but found Float」（dense 通道失败，
        检索静默降级为 sparse-only，trace 里落成 `DENSE_FAILED`/`PARTIAL`），重则**原生段错误**
        （exit 139，无 Python 回溯）。两者都不是「本机偶发」：它们是同一处并发缺陷的不同落点。
        把前向放进锁里即消除——单线程语义完全不变，只是不再允许两个前向重叠。

        **取值顺序不可颠倒**：必须在**进锁之前**先把 `self.model` 取出来（`model` 属性自己会
        加锁完成首次加载）。`threading.Lock` **不可重入**，而 `self.model` 的加载分支同样要取
        `_LOAD_LOCK`；若先持锁再走 `self.model`，**首次**编码（模型尚未加载）就会在同一线程上
        二次取锁而**永久阻塞**——没有异常、没有 CPU、没有内存增长，只是整条链停在那里。
        实测形态：`retrieval.embedding.get_embedding_model().encode([...])` 冷启动后不返回
        （`faulthandler` 落在本文件 `with _LOAD_LOCK:` 那一行）。加载完成后再编码不触发，
        因此这个缺陷只在「本次进程里第一次编码」时出现——正是 `evals/test_analyzer` 撞到的那个点。
        """
        if not texts:
            return []
        model = self.model
        with _LOAD_LOCK:
            output = model.encode(
                texts,
                return_dense=True,
                return_sparse=False,
                return_colbert_vecs=False,
            )
        dense = output["dense_vecs"]
        # 转为 list[list[float]]（可能已是 numpy array）
        if hasattr(dense, "tolist"):
            return dense.tolist()
        return dense


# ── 模块级单例 ──

_default_model: EmbeddingModel | None = None


def get_embedding_model() -> EmbeddingModel:
    """返回模块级单例 EmbeddingModel（惰性初始化，线程安全）。

    单例本身也要加锁：否则两个线程可能各拿到一个**不同的** `EmbeddingModel`，
    于是 `model` 属性上的锁形同虚设——两份实例、两次加载。
    """
    global _default_model
    if _default_model is None:
        with _LOAD_LOCK:
            if _default_model is None:
                _default_model = EmbeddingModel()
    return _default_model


def set_embedding_model(model: EmbeddingModel) -> None:
    """注入自定义 EmbeddingModel（eval 用）。"""
    global _default_model
    with _LOAD_LOCK:
        _default_model = model
