from pydantic import BaseModel, EmailStr


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class GoogleAuthToken(BaseModel):
    access_token: str
    token_type: str = "bearer"
    is_new_user: bool = False


class TokenData(BaseModel):
    user_id: int
    email: str
    username: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class GoogleAuthRequest(BaseModel):
    credential: str  # Google ID token
    mode: str = "signin"  # "signin" or "signup"


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str
    password: str


class MessageResponse(BaseModel):
    message: str
