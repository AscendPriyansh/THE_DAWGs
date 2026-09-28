from django.contrib.auth import authenticate, login, logout
from django.views.decorators.csrf import ensure_csrf_cookie
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from apps.accounts.serializers import LoginSerializer, UserSerializer


@api_view(["POST"])
@permission_classes([AllowAny])
@ensure_csrf_cookie
def login_view(request):
    serializer = LoginSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    email = serializer.validated_data["email"].strip().lower()
    password = serializer.validated_data["password"]

    user = authenticate(request, username=email, password=password)
    if user is None:
        return Response(
            {"detail": "Invalid email or password."},
            status=status.HTTP_401_UNAUTHORIZED,
        )

    if not user.is_active:
        return Response(
            {"detail": "Account is disabled."},
            status=status.HTTP_403_FORBIDDEN,
        )

    login(request, user)
    return Response({
        "detail": "Successfully authenticated.",
        "user": UserSerializer(user).data,
    })


@api_view(["POST"])
@permission_classes([AllowAny])
def logout_view(request):
    logout(request)
    return Response({"detail": "Successfully logged out."})


@api_view(["GET"])
@permission_classes([AllowAny])
def me_view(request):
    if not request.user.is_authenticated:
        return Response({"authenticated": False, "user": None})
    return Response({
        "authenticated": True,
        "user": UserSerializer(request.user).data,
    })
