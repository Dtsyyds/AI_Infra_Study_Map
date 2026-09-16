请求体应该包含哪些字段？
成功响应应该返回哪些字段？
创建成功应该返回 200 还是 201？为什么？
如果 LocalEmbedder 加载失败，应该返回什么状态码？
为什么不能在每一次路由调用中都执行 LocalEmbedder()？
如果两个请求同时写入同一个索引文件，单纯的原子写入能否完全解决问题？

请求体包含的字段：用户输入的文本
成功响应返回的字段：输出的文本
创建成功返201，因为这是一个创建操作，而201状态码表示资源已经被创建。
LocalEmbedder 加载失败应该返回500状态码，表示服务器内部错误。
LocalEmbedder() Embedding 模型加载昂贵。更合理的是：服务启动时加载一次；由应用生命周期管理；路由通过依赖注入获得实例；服务关闭时释放相关资源。路由负责 HTTP 协议转换，不负责重复加载模型。
单纯的原子写入不能解决写入抢占资源问题，如果两个同时写入，会出现覆盖等问题，比较合理的是使用锁机制，或者在写入前检查文件是否存在，如果不存在则创建并写入，如果存在则需要等待或者抛出异常。

为什么当前请求返回的是 404，而不是 422？
如果路由改成 /v1/indexes，但 POST 时不发送 JSON，会返回什么状态码？build_document_index() 会不会执行？
如果路由和 JSON 都正确，但 input_path 不存在，这个错误会经过哪些层？
为什么磁盘中的索引必须包含 records，但 HTTP 响应不应该返回它？
为什么测试要写成 with TestClient(app)，而不是只创建 TestClient(app)？

1. 当前请求返回 404 是因为只注册了 /v1/indexes 路由，而没有注册 /v1/indexs。FastAPI 自己返回 404,业务函数没有运行。422 抛出是请求体不正确，比如缺少字段或者 JSON 格式不正确。
2. 不发送 JSON 会返回 422,因为这时iu请求体h不正确，build_document_index() 不会执行。
3. 匹配路由，Pydantic 校验成功，build_document_index() 执行，load_text_file() 但找不到文件会抛出 FileNotFoundError,会被 FastAPI 捕获，返回 404。
4. 磁盘中包含 records 是为了后续拼接检索，不应该通过 HTTP 响应暴露普通接口。
5. 进入 with 会启动 lifespan, 退出会关闭 lifespan, 测试框架会保证在测试前后执行。

为什么 create_app() 接受 embedder_factory，而路由使用 app.state.embedder？
create_app() 接收工厂函数，是为了延迟创建昂贵模型，并允许测试注入 FakeEmbedder。服务启动进入 lifespan 时调用工厂创建一次实例，然后保存到 app.state.embedder，所有路由复用该实例，避免每个请求重复加载模型。
embedder_factory：负责创建；
app.state.embedder：负责保存和共享创建结果。

如果模型在 embedder_factory() 中加载失败，HTTP 请求还能进入路由吗？
当前实现是在 lifespan 的 yield 之前加载模型。如果工厂抛出异常，应用启动过程失败，Uvicorn 不会进入接收请求的阶段，因此请求无法进入路由。这是一种 required dependency 的 fail-fast 策略。

机器只能容纳两个模型实例时，能不能直接配置四个 Uvicorn worker？为什么？
Uvicorn 的多个 worker 是独立进程，每个进程都会执行一次 lifespan，因此四个 worker 会加载四份模型。如果机器内存只能容纳两份模型，配置四个 worker 可能导致 OOM、频繁交换内存甚至启动失败。模型服务的 worker 数需要同时考虑模型内存、并发能力和 CPU/GPU 资源，而不是简单设置成 CPU 核数
1. FastAPI 应用启动时，会调用 lifespan 管理器，在里面创建 embedder_factory() 返回的实例，并存入 app.state.embedder。
2. 不会，因为 FastAPI 应用启动失败时会抛出异常，导致请求无法进入路由。
3. 不能，h超出限制

为什么启动失败不能由普通路由的 exception_handler 转成 500？
普通 exception_handler 处理的是 HTTP 请求执行期间的异常。Embedder 在 lifespan 的 yield 前加载，此时应用尚未进入请求处理阶段，没有 Request 和 Response 上下文，因此不能通过路由异常处理器转换成 HTTP 500。

如果 Embedder 是可选功能，而其他接口仍需运行，是否还应该 fail-fast？
生产环境中谁负责在启动失败后重试——FastAPI、Uvicorn，还是 systemd/Kubernetes？
FastAPI：应用逻辑
Uvicorn：运行 ASGI 应用
systemd / Docker / Kubernetes：进程重启与调度
应用本身不应该无限循环重启模型。单实例部署通常由 systemd 或 Docker restart policy 重启；容器集群中由 Kubernetes 根据容器退出状态和探针执行重启、摘流和重新调度。

1. 启动失败时，FastAPI 应用还没有正常启动，无法通过路由转化
2. 应该，其他接口可能需要 embedder
3. Uvicorn 负责，因为它控制进程生命周期

为什么 422 响应也能执行 Middleware 中 await call_next() 后面的代码？
关键不只是“生成了 HTTP 请求”，而是异常被处理器转换成了 Response：
Middleware
→ await call_next(request)
→ Pydantic 抛出 RequestValidationError
→ 已注册的异常处理器捕获
→ 返回 422 JSONResponse
→ call_next() 得到 Response
→ Middleware 添加响应头
另外，这里的 X-Request-ID 是返回给当前上游调用者的响应头。要继续传给另一个下游服务，还需要在发起下游 HTTP 请求时主动加入该 Header。

如果业务代码抛出一个没有注册处理器的 RuntimeError，call_next() 一定能返回 Response 吗？
真实服务器通常最终对客户端生成一个默认 500，但测试中的 TestClient 默认会重新抛出服务端异常，方便开发者看到完整堆栈。

request.state.request_id 与响应头 X-Request-ID 分别服务于谁？
request.state.request_id：当前服务内部的单请求上下文；
X-Request-ID：跨 HTTP 边界传递给调用者；
调用其他服务时，也应将该 ID 放入出站请求头，串联整条调用链。

1. 当前 422 响应是e因为 overlap=-1 在 Pydantic 校验阶段就失败了，即使路由没有执行但生成了 http 请求，因此将 request_id 透传给下游服务
2. 应该有默认i情况返回
3. request.state.request_id 是服务内部单次请求上下文 X-Request-ID 是服务端将返回的响应头
