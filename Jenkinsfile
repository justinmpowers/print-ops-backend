pipeline {
    agent any
    
    environment {
        REGISTRY = 'ghcr.io'
        IMAGE_NAME = 'justinmpowers/j3d-backend'
        DOCKER_CREDENTIALS = credentials('github-container-registry')
    }
    
    options {
        // Only keep last 10 builds
        buildDiscarder(logRotator(numToKeepStr: '10'))
    }
    
    triggers {
        // Trigger on GitHub push events via webhook
        githubPush()
    }
    
    stages {
        stage('Checkout') {
            steps {
                checkout scm
            }
        }
        
        stage('Extract Version') {
            steps {
                script {
                    if (!fileExists('VERSION')) {
                        error "VERSION file not found in workspace. Please ensure a VERSION file is present before running the build."
                    }

                    try {
                        env.VERSION = readFile('VERSION').trim()
                    } catch (err) {
                        error "Failed to read VERSION file: ${err}"
                    }
                    echo "Building version: ${env.VERSION}"
                }
            }
        }
        
        stage('Build Docker Image') {
            steps {
                script {
                    docker.withRegistry("https://${env.REGISTRY}", 'github-container-registry') {
                        def imageTagVersion = "${env.REGISTRY}/${env.IMAGE_NAME}:${env.VERSION}"
                        def imageTagLatest = "${env.REGISTRY}/${env.IMAGE_NAME}:latest"

                        sh """
                            docker buildx build \\
                              --platform linux/amd64,linux/arm64 \\
                              --build-arg VERSION=${env.VERSION} \\
                              -t ${imageTagVersion} \\
                              -t ${imageTagLatest} \\
                              --push \\
                              .
                        """
                    }
                }
            }
        }
    }
    
    post {
        success {
            echo "Successfully built and pushed ${env.IMAGE_NAME}:${env.VERSION}"
        }
        failure {
            echo "Build failed for ${env.IMAGE_NAME}"
        }
        always {
            cleanWs()
        }
    }
}
