pipeline {
    agent any

    environment {
        PROJECT_NAME = 'wisepencloud'
        DOCKER_REGISTRY = 'local'
        IMAGE_TAG = sh(script: 'git rev-parse --short HEAD', returnStdout: true).trim()
        COMPOSE_FILE_PATH = 'docker-compose-app.yml'
    }

    parameters {
        string(name: 'BRANCH_NAME', defaultValue: 'main', description: '要构建的 Git 分支')
        choice(name: 'DEPLOY_MODE', choices: ['prod', 'preview'], description: '部署模式')
    }

    stages {
        stage('1. 拉取代码 (Checkout)') {
            steps {
                echo "开始拉取 ${params.BRANCH_NAME} 分支代码..."
                checkout scm
                script {
                    env.DEPLOY_PROJECT_NAME = params.DEPLOY_MODE == 'preview'
                            ? "${env.PROJECT_NAME}-preview"
                            : "${env.PROJECT_NAME}"
                }
                echo "代码拉取成功，当前构建版本 TAG: ${IMAGE_TAG}"
                echo "当前部署模式: ${params.DEPLOY_MODE}"
                echo "当前镜像项目名: ${DEPLOY_PROJECT_NAME}"
            }
        }

        stage('2. 构建 Docker 镜像 (Docker Build)') {
            failFast true

            parallel {
                stage('Chat Service') {
                    steps {
                        script {
                            sh """
                                docker build \\
                                    -t ${DOCKER_REGISTRY}/${DEPLOY_PROJECT_NAME}-chat:${IMAGE_TAG} \\
                                    --build-arg SERVICE_DIR=wisepen-chat-service \\
                                    --build-arg SERVICE_PKG=chat \\
                                    --build-arg SERVICE_PORT=9200 \\
                                    -f Dockerfile .
                            """
                        }
                    }
                }
            }
        }

        stage('3. 部署 (Deploy)') {
            environment {
                NACOS_USER = credentials('nacos-username')
                NACOS_PWD  = credentials('nacos-password')
            }
            steps {
                script {
                    echo "开始部署最新版本: ${IMAGE_TAG} ..."
                    sh """
                    if ! command -v docker-compose &> /dev/null; then
                        echo "容器内缺失 docker-compose，正在自动下载..."
                        curl -L -# -o /usr/local/bin/docker-compose "https://github.com/docker/compose/releases/latest/download/docker-compose-\$(uname -s)-\$(uname -m)"
                        chmod +x /usr/local/bin/docker-compose
                    fi

                    export APP_VERSION=${IMAGE_TAG}
                    export PROJECT_NAME=${DEPLOY_PROJECT_NAME}
                    export DOCKER_REGISTRY=${DOCKER_REGISTRY}
                    export NACOS_USERNAME=\${NACOS_USER}
                    export NACOS_PASSWORD=\${NACOS_PWD}

                    COMPOSE_FILES="-f ${COMPOSE_FILE_PATH}"

                    if [ "${params.DEPLOY_MODE}" = "preview" ]; then
                        export COMPOSE_PROJECT_NAME=${DEPLOY_PROJECT_NAME}
                        export CHAT_CONTAINER_NAME=wisepen-chat-service-preview
                        export CHAT_SERVICE_ALIAS=chat-service
                        export PROFILE=prod
                    else
                        export COMPOSE_PROJECT_NAME=${PROJECT_NAME}
                        export CHAT_CONTAINER_NAME=wisepen-chat-service
                        export CHAT_SERVICE_ALIAS=chat-service
                        export PROFILE=prod
                    fi

                    docker-compose \$COMPOSE_FILES up -d --remove-orphans
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
            echo "构建与部署完成！版本: ${IMAGE_TAG}"
        }
        failure {
            echo "流水线执行失败，请检查 Jenkins 控制台报错日志！"
        }
    }
}