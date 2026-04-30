pipeline {
    agent any

    environment {
        PROJECT_NAME = 'wisepencloud'
        DOCKER_REGISTRY = 'local'
        IMAGE_TAG = sh(script: 'git rev-parse --short HEAD', returnStdout: true).trim()
        COMPOSE_FILE_PATH = 'docker-compose-app.yml'
    }

    parameters {
        string(name: 'BRANCH_NAME', defaultValue: 'main', description: '选择需要构建的 Git 分支')
    }

    stages {
        stage('1. 拉取代码 (Checkout)') {
            steps {
                echo "开始拉取 ${params.BRANCH_NAME} 分支代码..."
                checkout scm
                echo "代码拉取成功，当前构建版本 TAG: ${IMAGE_TAG}"
            }
        }

        stage('2. 构建 Docker 镜像 (Docker Build)') {
            failFast true

            parallel {
                stage('Chat Service') {
                    steps {
                        script {
                            sh "docker build -t ${DOCKER_REGISTRY}/${PROJECT_NAME}-chat:${IMAGE_TAG} -f Dockerfile ."
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
                    export DOCKER_REGISTRY=${DOCKER_REGISTRY}
                    export NACOS_USERNAME=\${NACOS_USER}
                    export NACOS_PASSWORD=\${NACOS_PWD}

                    # 网络兼容
                    # 检测到遗留 cloud-infra_cloud-net 时叠加 legacy-net overlay
                    # 老中间件全部下线后，本脚本可以与docker-compose-app.legacy-net.yml文件、部署脚本中的探测分支一并删除
                    docker network create wisepen-net 2>/dev/null || true
                    COMPOSE_FILES="-f ${COMPOSE_FILE_PATH}"
                    if docker network inspect cloud-infra_cloud-net >/dev/null 2>&1; then
                        echo "检测到遗留网络 cloud-infra_cloud-net，叠加 docker-compose-app.legacy-net.yml"
                        COMPOSE_FILES="\$COMPOSE_FILES -f docker-compose-app.legacy-net.yml"
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
