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
                    env.VERSION = readFile('VERSION').trim()
                    echo "Building version: ${env.VERSION}"
                }
            }
        }
        
        stage('Build Docker Image') {
            steps {
                script {
                    docker.withRegistry("https://${env.REGISTRY}", 'github-container-registry') {
                        def customImage = docker.build(
                            "${env.REGISTRY}/${env.IMAGE_NAME}:${env.VERSION}",
                            "--build-arg VERSION=${env.VERSION} ."
                        )
                        customImage.push()
                        customImage.push('latest')
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
