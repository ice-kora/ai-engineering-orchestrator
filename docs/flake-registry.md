# Flake Registry — 已知间歇性测试失败登记（治理项，不阻塞阶段推进）

> 与 `docs/runtime-errata-v1.1.md`（spec-vs-reality 事实覆盖层）相区分：本清单登记**测试稳定性治理项**。

## FLAKE-001：test_git_worktree.py::test_forensic_retention_dry_run 间歇失败

| 字段 | 内容 |
|---|---|
| 登记日期 | 2026-09-10 |
| 首次实证 | P2-01 Hotfix Final Full Regression（commit 6c437e3，单次完整 `pytest tests` → 69/7/1） |
| 现象 | 全量运行中偶发失败；失败后立即单套件复跑 **2/2 全过**（多次复现该模式） |
| 根因分类 | errata E-06/E-07 间歇类：agy 子进程退出时序 × Windows 文件锁，偶发延迟 `git worktree remove`；同类的既有对策（清理重试一次 + 落点断言）已在该套件，但极端时序下仍可穿透 |
| 影响面 | 仅测试基础设施稳定性；**零生产逻辑关联**（热修仅触及 orchestrator/materialize.py 与 reconcile.py） |
| Gate 处置 | `KNOWN_FLAKE_WAIVER = ACCEPTED`（GPT Final Gate，2026-09-10）；Final Regression 结果保持 69/7/1 真实记录，不改写 |
| 治理方向（后续，不阻塞 P2-02） | ① 失败重试策略（pytest-rerunfailures 类，按标记限定该用例）；② 清理重试增加指数退避 + 最终断言前二次探测；③ 若根治则关闭本条 |
| 状态 | OPEN（治理挂账） |
