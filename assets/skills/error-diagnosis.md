# 报错诊断
<!-- tools: get_logs, queue_list, list_workflows, check_workflow, control_instance -->
按固定流程定位 ComfyUI 报错，先归类再给结论。

诊断顺序：
1. get_logs 读最近 50~100 行，找最后一个 traceback 的最底下一行（真正的错误类型）；
2. 归类：CUDA out of memory（降分辨率/batch）｜ImportError/ModuleNotFoundError（缺节点或依赖）｜FileNotFoundError（模型/路径缺失）｜Connection refused（实例没起来）；
3. 节点报错先 list_workflows + check_workflow 确认是不是自定义节点缺失，再建议装对应插件；
4. 同一错误重复出现时查是不是模型文件损坏（对照文件大小）或显存被其它进程占着；
5. 结论格式：错误类型 → 直接原因 → 一条可执行的修复动作。不确定就明说不确定，不编原因。
