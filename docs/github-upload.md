# 一键上传说明

先安装 Git for Windows 并完成 GitHub 登录及 git user.name / user.email 配置。双击根目录 UPLOAD_TO_GITHUB.cmd，默认上传到 https://github.com/yangaier0920-afk/mongo-consistency-project。脚本校验文件、克隆现有仓库至临时目录、创建 submission/final-时间 分支、整理文件、提交并推送。登录可能需要浏览器交互；上传结束后仍需在 GitHub 检查分支并合并到 main。

脚本不强制推送、不直接修改 main，也不删除原工作目录。旧 experiments/results/setup/report 等明确列出的项目材料移到新分支 archive/github-before-时间/；未知文件保留。新 README 只指向当前入口和正式结果，旧文件留作历史，不进入当前统计。原有同名配置、README 和脚本等由新包替换，其旧版本仍可从 Git 历史取得。

此次上传脚本未实际连接远端测试或推送；本地已进行语法和暂存迁移流程验证。GitHub URL、权限及认证需由维护者提供。失败时保留临时克隆便于检查，不反复强制推送。若本包任何受校验文件被编辑，应先复核并重新生成发布清单，再上传，不应直接绕过校验。

仓库更新与 Canvas 提交是两件事：最终 PDF 仍须按课程要求完成并提交。此包只包含中文报告草稿，不能标称报告已最终定稿。
