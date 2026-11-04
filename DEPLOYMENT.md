# 部署验收记录

北京时间2026-11-04更新。

- 仓库代码已部署到main。
- 本地与GitHub云端33项测试均通过，涵盖上涨、下降、小涨、数据缺失、5元边界、北京时间自然日、去重与有限重试。
- 公开日历及浙江92号参考价已读取原文核实并写入data/check.json。
- [真实数据处理工作流](https://github.com/garfieldjim123-ux/fuel-info/actions/runs/41793404763)执行成功；不在提前3日窗口时正确不发信。
- “宁波92号油价数据检查”定时任务已创建并启用：Asia/Shanghai，每天09:00，首次计划2026-11-05 09:00。尚未产生首次定时运行记录；调度成功不代表实际执行或邮件送达。
- [真实测试邮件流程](https://github.com/garfieldjim123-ux/fuel-info/actions/runs/41795711779)已触发，云端33项测试通过，但发送前置校验报缺少SMTP_USER和SMTP_PASSWORD。没有向SMTP提交邮件。
- SMTP连通性、云端持久化发送状态写入及收件箱送达尚未验收；代码中的故障处理已通过隔离测试。

当前结论：代码、数据触发处理和每日定时任务已配置；邮件服务尚未启用，不得宣称端到端验收完成。

下一步：用户在GitHub Settings → Secrets and variables → Actions填写SMTP_USER和SMTP_PASSWORD。不要将QQ授权码放到聊天、代码或普通Variables。随后运行Fuel reminder，勾选test_email，或由助手提交明确标记的测试请求以触发同一路径。失败的缺配置测试没有发送邮件，不消耗成功测试的去重状态。

无新增付费服务、付费Runner或付费搜索API。使用公开仓库标准Linux Actions免费方案及现有ChatGPT任务额度。修改门槛、暂停和删除步骤见README.md。定时任务暂只记录创建与启用状态，首次实际云端运行需另外核查。
