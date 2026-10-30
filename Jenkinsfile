pipeline {
    agent any

    options {
        skipDefaultCheckout(true)
        disableConcurrentBuilds()
        timestamps()
    }

    environment {
        PROJECT_NAME = 'wisepencloud'
        DOCKER_REGISTRY = 'local'
        COMPOSE_FILE_PATH = 'docker-compose-app.yml'
    }

    parameters {
        string(name: 'BRANCH_NAME', defaultValue: 'main', description: '选择需要构建的 Git 分支')
        booleanParam(name: 'DEPLOY_AFTER_BUILD', defaultValue: true, description: '构建成功后是否执行部署')
        booleanParam(name: 'PUSH_TO_REGISTRY', defaultValue: false, description: '是否额外上传镜像到用户指定的制品仓')
        string(name: 'REGISTRY_HOST', defaultValue: '', description: '制品仓主机名和可选端口，不包含协议或路径')
        string(name: 'REGISTRY_NAMESPACE', defaultValue: '', description: '制品仓命名空间，例如 wisepen')
        string(name: 'PYPI_INDEX_URL', defaultValue: 'https://mirrors.aliyun.com/pypi/simple/', description: 'Python 包索引地址，默认使用中国大陆镜像')
        booleanParam(name: 'DEPLOY_CHAT_SERVICE', defaultValue: true, description: '构建并部署 chat-service')
        booleanParam(name: 'DEPLOY_MCP_SERVICE', defaultValue: true, description: '构建并部署 mcp-service')
        booleanParam(name: 'DEPLOY_SANDBOX_SERVICE', defaultValue: true, description: '构建并部署 sandbox-service')
        choice(name: 'SANDBOX_RUNTIME_TARGET', choices: ['LOCAL', 'REMOTE'], description: '沙箱容器运行位置；仅在部署 sandbox-service 时生效')
        string(name: 'SANDBOX_REMOTE_DOCKER_HOST', defaultValue: '', description: 'REMOTE 时必填，例如 tcp://10.0.0.22:2375')
        string(name: 'SANDBOX_REMOTE_HTTP_HOST', defaultValue: '', description: 'REMOTE 时必填，只填 IP 或主机名，例如 10.0.0.22')
    }

    stages {
        stage('1. 拉取代码 (Checkout)') {
            steps {
                script {
                    def remoteUrl = scm.userRemoteConfigs[0].url
                    echo "开始从 ${remoteUrl} 拉取 ${params.BRANCH_NAME} 分支代码..."
                    deleteDir()
                    git branch: params.BRANCH_NAME, url: remoteUrl
                    env.GIT_COMMIT_FULL = sh(script: 'git rev-parse HEAD', returnStdout: true).trim()
                    env.IMAGE_TAG = sh(script: 'git rev-parse --short HEAD', returnStdout: true).trim()
                    sh 'git status --short --branch'
                    echo "代码拉取成功，Commit: ${env.GIT_COMMIT_FULL}，镜像 TAG: ${env.IMAGE_TAG}"
                }
            }
        }

        stage('1.5 确认部署服务 (Select Services)') {
            steps {
                script {
                    def selectedServices = []
                    if (params.DEPLOY_CHAT_SERVICE) {
                        selectedServices << 'chat-service'
                    }
                    if (params.DEPLOY_MCP_SERVICE) {
                        selectedServices << 'mcp-service'
                    }
                    if (params.DEPLOY_SANDBOX_SERVICE) {
                        selectedServices << 'sandbox-service'
                    }
                    if (selectedServices.isEmpty()) {
                        error '至少需要选择一个要构建的服务。'
                    }
                    env.SELECTED_COMPOSE_SERVICES = selectedServices.join(' ')
                    echo "本次选择构建的服务: ${env.SELECTED_COMPOSE_SERVICES}"
                }
            }
        }

        stage('2. 构建 Docker 镜像') {
            failFast true

            parallel {
                stage('Chat Service') {
                    when {
                        expression { return params.DEPLOY_CHAT_SERVICE }
                    }
                    steps {
                        script {
                            sh """
                                docker build \\
                                    -t ${DOCKER_REGISTRY}/${PROJECT_NAME}-chat:${IMAGE_TAG} \\
                                    --build-arg SERVICE_DIR=wisepen-chat-service \\
                                    --build-arg SERVICE_PKG=chat \\
                                    --build-arg SERVICE_PORT=19904 \\
                                    --build-arg SERVICE_PROJECT=wisepen-chat-service \\
                                    --build-arg PYPI_INDEX_URL="\${PYPI_INDEX_URL}" \\
                                    -f Dockerfile .
                            """
                        }
                    }
                }
                stage('MCP Service') {
                    when {
                        expression { return params.DEPLOY_MCP_SERVICE }
                    }
                    steps {
                        script {
                            sh """
                                docker build \\
                                    -t ${DOCKER_REGISTRY}/${PROJECT_NAME}-mcp:${IMAGE_TAG} \\
                                    --build-arg SERVICE_DIR=wisepen-mcp-service \\
                                    --build-arg SERVICE_PKG=wisepen_mcp \\
                                    --build-arg SERVICE_PORT=19911 \\
                                    --build-arg SERVICE_PROJECT=wisepen-mcp-service \\
                                    --build-arg PYPI_INDEX_URL="\${PYPI_INDEX_URL}" \\
                                    -f Dockerfile .
                            """
                        }
                    }
                }
                stage('Sandbox Service') {
                    when {
                        expression { return params.DEPLOY_SANDBOX_SERVICE }
                    }
                    steps {
                        script {
                            sh """
                                docker build \\
                                    -t ${DOCKER_REGISTRY}/${PROJECT_NAME}-sandbox:${IMAGE_TAG} \\
                                    --build-arg SERVICE_DIR=wisepen-sandbox-service \\
                                    --build-arg SERVICE_PKG=sandbox \\
                                    --build-arg SERVICE_PORT=19915 \\
                                    --build-arg SERVICE_PROJECT=wisepen-sandbox-service \\
                                    --build-arg PYPI_INDEX_URL="\${PYPI_INDEX_URL}" \\
                                    -f Dockerfile .
                            """
                        }
                    }
                }

            }
        }

        stage('2.5 上传镜像到制品仓 (Push Registry)') {
            when {
                expression { return params.PUSH_TO_REGISTRY }
            }
            steps {
                script {
                    if (!params.REGISTRY_HOST?.trim() || params.REGISTRY_HOST.contains('://') || params.REGISTRY_HOST.contains('/')) {
                        error 'REGISTRY_HOST 必须填写主机名和可选端口，不能包含协议或路径。'
                    }
                    if (!params.REGISTRY_NAMESPACE?.trim()) {
                        error '启用制品仓上传时必须填写 REGISTRY_NAMESPACE。'
                    }
                    withCredentials([
                        usernamePassword(
                            credentialsId: 'docker-registry-credentials',
                            usernameVariable: 'REGISTRY_USERNAME',
                            passwordVariable: 'REGISTRY_PASSWORD'
                        )
                    ]) {
                        withEnv([
                            "REGISTRY_HOST=${params.REGISTRY_HOST.trim()}",
                            "REGISTRY_NAMESPACE=${params.REGISTRY_NAMESPACE.trim()}"
                        ]) {
                            sh '''
                            set -eu
                            DOCKER_CONFIG="$(mktemp -d)"
                            export DOCKER_CONFIG
                            trap 'rm -rf "$DOCKER_CONFIG"' EXIT
                            printf '%s' "$REGISTRY_PASSWORD" | docker login "$REGISTRY_HOST" \
                                --username "$REGISTRY_USERNAME" --password-stdin

                            for service in $SELECTED_COMPOSE_SERVICES; do
                                case "$service" in
                                    chat-service) image_name=chat ;;
                                    mcp-service) image_name=mcp ;;
                                    sandbox-service) image_name=sandbox ;;
                                    *) echo "未知服务: $service"; exit 1 ;;
                                esac
                                source_image="${DOCKER_REGISTRY}/${PROJECT_NAME}-${image_name}:${IMAGE_TAG}"
                                target_image="${REGISTRY_HOST}/${REGISTRY_NAMESPACE}/${PROJECT_NAME}-${image_name}:${IMAGE_TAG}"
                                docker tag "$source_image" "$target_image"
                                docker push "$target_image"
                            done
                            '''
                        }
                    }
                }
            }
        }

        stage('3. 部署 (Deploy)') {
            when {
                expression { return params.DEPLOY_AFTER_BUILD }
            }
            environment {
                NACOS_USER = credentials('nacos-username')
                NACOS_PWD  = credentials('nacos-password')
            }
            steps {
                script {
                    echo "开始部署最新版本: ${IMAGE_TAG}，服务: ${env.SELECTED_COMPOSE_SERVICES} ..."
                    sh """
                    set -eu
                    docker compose version

                    export APP_VERSION=${IMAGE_TAG}
                    export DOCKER_REGISTRY=${DOCKER_REGISTRY}
                    export NACOS_USERNAME=\${NACOS_USER}
                    export NACOS_PASSWORD=\${NACOS_PWD}

                    if [ "\${DEPLOY_SANDBOX_SERVICE}" = "true" ]; then
                        case "\${SANDBOX_RUNTIME_TARGET}" in
                            LOCAL)
                                export SANDBOX_DOCKER_API_HOST="unix:///var/run/docker.sock"
                                export SANDBOX_DOCKER_HTTP_HOST="host.docker.internal"
                                ;;
                            REMOTE)
                                case "\${SANDBOX_REMOTE_DOCKER_HOST}" in
                                    tcp://*:* ) ;;
                                    * ) echo "SANDBOX_REMOTE_DOCKER_HOST 必须使用 tcp://host:port 格式"; exit 1 ;;
                                esac
                                case "\${SANDBOX_REMOTE_HTTP_HOST}" in
                                    ''|*[!A-Za-z0-9.-]* ) echo "SANDBOX_REMOTE_HTTP_HOST 只能填写 IP 或主机名"; exit 1 ;;
                                esac
                                export SANDBOX_DOCKER_API_HOST="\${SANDBOX_REMOTE_DOCKER_HOST}"
                                export SANDBOX_DOCKER_HTTP_HOST="\${SANDBOX_REMOTE_HTTP_HOST}"
                                ;;
                            *)
                                echo "无效的 SANDBOX_RUNTIME_TARGET: \${SANDBOX_RUNTIME_TARGET}"
                                exit 1
                                ;;
                        esac
                        echo "沙箱容器运行位置: \${SANDBOX_RUNTIME_TARGET}"
                    fi

                    docker network create wisepen-net 2>/dev/null || true

                    # 清理本轮选中服务的规范容器
                    for service in ${env.SELECTED_COMPOSE_SERVICES}; do
                        case "\$service" in
                            chat-service) container_name=wisepen-chat-service ;;
                            mcp-service) container_name=wisepen-mcp-service ;;
                            sandbox-service) container_name=wisepen-sandbox-service ;;
                            *) echo "未知 Compose 服务: \$service"; exit 1 ;;
                        esac
                        docker rm -f "\$container_name" >/dev/null 2>&1 || true
                    done

                    docker compose \
                        -p wisepen-cloud-ai \
                        -f ${COMPOSE_FILE_PATH} \
                        up -d --no-build --force-recreate --remove-orphans \
                        ${env.SELECTED_COMPOSE_SERVICES}

                    if [ "\${DEPLOY_SANDBOX_SERVICE}" = "true" ]; then
                        echo "验证 sandbox-service 的 Docker daemon 连接（15 秒超时）..."
                        timeout 15s docker exec wisepen-sandbox-service \
                            docker info --format 'Server={{.Name}} Docker={{.ServerVersion}} Containers={{.Containers}}'
                    fi
                    """
                }
            }
        }
    }

    post {
        always {
            echo "执行 Docker 垃圾回收..."
            sh 'docker image prune -f'
        }
        success {
            script {
                echo(params.DEPLOY_AFTER_BUILD
                    ? "构建与部署完成！版本: ${IMAGE_TAG}"
                    : "构建完成，已按 build-only 模式跳过部署！版本: ${IMAGE_TAG}")
            }
        }
        failure {
            echo "流水线执行失败，请检查 Jenkins 控制台报错日志！"
        }
    }
}
