pipeline {
    agent { label 'docker-buildx' }
    
    environment {
        REGISTRY = 'ghcr.io'
        IMAGE_NAME = 'justinmpowers/j3d-backend'
    }
    
    options {
        // Only keep last 10 builds
        buildDiscarder(logRotator(numToKeepStr: '10'))
    }
    
    triggers {
        // Trigger on GitHub push events via webhook for relevant file changes
        // Note: Path filtering must be configured in the webhook or SCM trigger configuration
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
                    def imageTagVersion = "${env.REGISTRY}/${env.IMAGE_NAME}:${env.VERSION}"

                    withCredentials([usernamePassword(credentialsId: 'github-container-registry', passwordVariable: 'DOCKER_PASSWORD', usernameVariable: 'DOCKER_USERNAME')]) {
                        sh """
                            # Authenticate to the container registry for buildx
                            printf '%s' "\${DOCKER_PASSWORD}" | docker login "${env.REGISTRY}" -u "\${DOCKER_USERNAME}" --password-stdin

                            # Set up cache directories
                            CACHE_DIR=.buildx-cache
                            NEW_CACHE_DIR=.buildx-cache-new

                            mkdir -p "\${CACHE_DIR}"
                            rm -rf "\${NEW_CACHE_DIR}"

                            # Build and push the Docker image
                            docker buildx build \\
                              --platform linux/amd64,linux/arm64 \\
                              --build-arg VERSION=${env.VERSION} \\
                              --cache-from type=local,src=\${CACHE_DIR} \\
                              --cache-to type=local,dest=\${NEW_CACHE_DIR},mode=max \\
                              -t ${imageTagVersion} \\
                              --push \\
                              .

                            # Rotate cache directories
                            rm -rf "\${CACHE_DIR}"
                            mv "\${NEW_CACHE_DIR}" "\${CACHE_DIR}"
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
